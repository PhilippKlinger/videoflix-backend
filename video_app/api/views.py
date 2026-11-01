"""
API views for video upload, listing, detail, conversion progress, cache, and HLS streaming.
"""

import os
from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import views, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from video_app.models import Video
from .permissions import can_change_video, visible_videos
from .serializers import VideoSerializer
from django.core.cache import cache
from rest_framework.parsers import MultiPartParser, FormParser

# Only standard videos are cached; private uploads never enter the shared cache.
STANDARD_VIDEOS_CACHE_KEY = "all_videos"


class VideoClearCache(views.APIView):
    """
    API view for admins to clear the cached list of standard videos.
    """

    permission_classes = [IsAdminUser]

    def post(self, request):
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        return Response({"status": "Cache cleared"}, status=status.HTTP_200_OK)


class VideoUploadView(views.APIView):
    """
    API view for uploading a new video.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        cache_key = STANDARD_VIDEOS_CACHE_KEY
        serializer = VideoSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            # Admin uploads become standard videos, all others stay private.
            owner = None if request.user.is_staff else request.user
            serializer.save(owner=owner)
            cache.delete(cache_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class VideoConversionProgressView(views.APIView):
    """
    API view to retrieve the conversion progress and current resolution of a video.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, video_id):
        video = get_object_or_404(visible_videos(request.user), id=video_id)
        return Response(
            {
                "progress": video.conversion_progress,
                "current_resolution": video.current_resolution,
            }
        )


class VideoListView(views.APIView):
    """
    API view to list the videos the user may see.

    Standard videos come from the cache; own uploads (or, for admins, all uploads)
    are read from the database and never cached.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        standard_videos = self._get_standard_videos(request)
        uploads = visible_videos(request.user).filter(owner__isnull=False)
        if not uploads.exists():
            return Response(standard_videos)
        uploads = uploads.prefetch_related("resolutions")
        serialized_uploads = VideoSerializer(
            uploads, many=True, context={"request": request}
        ).data
        videos = list(serialized_uploads) + list(standard_videos)
        videos.sort(key=lambda video: video["created_at"], reverse=True)
        return Response(videos)

    def _get_standard_videos(self, request):
        cached_videos = cache.get(STANDARD_VIDEOS_CACHE_KEY)
        if cached_videos is not None:
            return cached_videos
        videos = (
            Video.objects.filter(owner__isnull=True)
            .prefetch_related("resolutions")
            .order_by("-created_at")
        )
        serialized_data = VideoSerializer(
            videos, many=True, context={"request": request}
        ).data
        cache.set(STANDARD_VIDEOS_CACHE_KEY, serialized_data, timeout=300)
        return serialized_data


class VideoDetailView(views.APIView):
    """
    API view to retrieve, update or delete a specific video instance.

    Videos the user may not see return 404; visible videos the user may not change return 403.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        video = get_object_or_404(visible_videos(request.user), pk=pk)
        serializer = VideoSerializer(video, context={"request": request})
        return Response(serializer.data)

    def patch(self, request, pk):
        video = self._get_changeable_video(request, pk)
        serializer = VideoSerializer(
            video, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            cache.delete(STANDARD_VIDEOS_CACHE_KEY)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        video = self._get_changeable_video(request, pk)
        video.delete()
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_changeable_video(self, request, pk):
        video = get_object_or_404(visible_videos(request.user), pk=pk)
        if not can_change_video(request.user, video):
            raise PermissionDenied("You may not change this video.")
        return video


def _get_video_or_404(user, video_id):
    video = visible_videos(user).filter(id=video_id).first()
    if not video:
        raise Http404("Video not found")
    return video


def _get_video_resolution_or_404(video, resolution):
    video_res = video.resolutions.filter(resolution=resolution).first()
    if not video_res:
        raise Http404("Resolution not found")
    return video_res


def _get_hls_file_path(base_path, filename):
    file_path = os.path.abspath(os.path.join(base_path, filename))
    if not file_path.startswith(os.path.abspath(settings.MEDIA_ROOT)):
        raise Http404("Invalid path")
    if not os.path.exists(file_path):
        raise Http404("File not found")
    return file_path


def _get_content_type(filename):
    if filename.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if filename.endswith(".ts"):
        return "video/mp2t"
    return "application/octet-stream"


class VideoHLSServeView(views.APIView):
    """
    API view to securely serve HLS playlist (.m3u8) and segment (.ts) files for streaming.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, video_id, resolution, filename):
        video = _get_video_or_404(request.user, video_id)
        video_res = _get_video_resolution_or_404(video, resolution)
        base_dir = os.path.dirname(video_res.converted_file.path)
        file_path = _get_hls_file_path(base_dir, filename)
        content_type = _get_content_type(filename)
        return FileResponse(open(file_path, "rb"), content_type=content_type)
