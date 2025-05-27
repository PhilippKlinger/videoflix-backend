from django.shortcuts import render, get_object_or_404
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from video_app.models import Video
from .serializers import VideoSerializer
from django.core.cache import cache
from rest_framework.parsers import MultiPartParser, FormParser


class VideoClearCache(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        cache_key = "all_videos"
        cache.delete(cache_key)
        return Response({"status": "Cache cleared"}, status=status.HTTP_200_OK)


class VideoUploadView(views.APIView):
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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cache_key = "all_videos"
        cached_videos = cache.get(cache_key)
        if cached_videos is not None:
            return Response(cached_videos)
        videos = Video.objects.all().order_by('-uploaded_at')
        serializer = VideoSerializer(videos, many=True)
        serialized_data = serializer.data
        cache.set(cache_key, serialized_data, timeout=300)
        return Response(serialized_data)


class VideoDetailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        video = get_object_or_404(Video, pk=pk)
        serializer = VideoSerializer(video)
        return Response(serializer.data)

    def patch(self, request, pk):
        video = get_object_or_404(Video, pk=pk)

        serializer = VideoSerializer(video, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            cache.delete("all_videos")
            cache.set(
                "all_videos",
                VideoSerializer(Video.objects.all(), many=True).data,
                timeout=300,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
