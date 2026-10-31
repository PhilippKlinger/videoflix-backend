"""
API views for video upload, listing, detail, conversion progress, cache, and HLS streaming.
"""

import os
from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from video_app.models import Video
from .serializers import VideoSerializer
from django.core.cache import cache
from rest_framework.parsers import MultiPartParser, FormParser


class VideoClearCache(views.APIView):
    """
    API view to clear the cached list of all videos.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        cache_key = "all_videos"
        cache.delete(cache_key)
        return Response({"status": "Cache cleared"}, status=status.HTTP_200_OK)


class VideoUploadView(views.APIView):
    """
    API view for uploading a new video.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        cache_key = "all_videos"
        serializer = VideoSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            cache.delete(cache_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class VideoConversionProgressView(views.APIView):
    """
    API view to retrieve the conversion progress and current resolution of a video.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, video_id):
        video = get_object_or_404(Video, id=video_id)
        return Response(
            {
                "progress": video.conversion_progress,
                "current_resolution": video.current_resolution,
            }
        )


class VideoListView(views.APIView):
    """
    API view to list all videos, using cache for better performance.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        cache_key = "all_videos"
        cached_videos = cache.get(cache_key)
        if cached_videos is not None:
            return Response(cached_videos)
        videos = Video.objects.all().order_by("-created_at")
        serializer = VideoSerializer(videos, many=True, context={"request": request})
        serialized_data = serializer.data
        cache.set(cache_key, serialized_data, timeout=300)
        return Response(serialized_data)


class VideoDetailView(views.APIView):
    """
    API view to retrieve or update a specific video instance.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        video = get_object_or_404(Video, pk=pk)
        serializer = VideoSerializer(video, context={"request": request})
        return Response(serializer.data)

    def patch(self, request, pk):
        video = get_object_or_404(Video, pk=pk)
        serializer = VideoSerializer(
            video, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            cache.delete("all_videos")
            cache.set(
                "all_videos",
                VideoSerializer(
                    Video.objects.all(), many=True, context={"request": request}
                ).data,
                timeout=300,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def _get_video_or_404(video_id):
    video = Video.objects.filter(id=video_id).first()
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
        video = _get_video_or_404(video_id)
        video_res = _get_video_resolution_or_404(video, resolution)
        base_dir = os.path.dirname(video_res.converted_file.path)
        file_path = _get_hls_file_path(base_dir, filename)
        content_type = _get_content_type(filename)
        return FileResponse(open(file_path, "rb"), content_type=content_type)
