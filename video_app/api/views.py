from django.shortcuts import render, get_object_or_404
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from video_app.models import Video, Profile
from .serializers import VideoSerializer
from django.core.cache import cache
from rest_framework.parsers import MultiPartParser, FormParser

# testweise clearing cache

class VideoClearCache(views.APIView):
    permission_classes = [IsAuthenticated]
    
    
    def post(self, request):
        cache_key = 'all_videos'
        cache.delete(cache_key)
        return Response({'status': 'Cache cleared'}, status=status.HTTP_200_OK)

class VideoUploadView(views.APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        cache_key = 'all_videos'
        serializer = VideoSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            # Cache löschen, um sicherzustellen, dass die VideoListe aktuell bleibt
            cache.delete(cache_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class VideoConversionProgressView(views.APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, video_id):
        video = get_object_or_404(Video, id=video_id)            
        return Response({
            'progress': video.conversion_progress,
            'current_resolution': video.current_resolution
        })

class VideoListView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Versuchen, Daten aus dem Cache zu laden
        cache_key = 'all_videos'
        cached_videos = cache.get(cache_key)
        
        if cached_videos is not None:
            # Wenn die Daten im Cache vorhanden sind, diese verwenden
            return Response(cached_videos)

        # Wenn keine Daten im Cache sind, aus der Datenbank laden
        videos = Video.objects.all()
        serializer = VideoSerializer(videos, many=True)
        serialized_data = serializer.data
        
        # Die Daten im Cache für zukünftige Anfragen speichern
        cache.set(cache_key, serialized_data, timeout=300)  # Timeout nach 300 Sekunden
        
        return Response(serialized_data)

class VideoDetailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        video = get_object_or_404(Video, pk=pk)
        serializer = VideoSerializer(video)
        return Response(serializer.data)

    def patch(self, request, pk):
        video = get_object_or_404(Video, pk=pk)
        favorited_by_ids = request.data.get('favorited_by')
        
        if favorited_by_ids is not None:
            profiles = Profile.objects.filter(id__in=favorited_by_ids)
            video.favorited_by.set(profiles)
            video.save()
            serializer = VideoSerializer(video)
            cache.delete('all_videos')  # Cache löschen
            cache.set('all_videos', VideoSerializer(Video.objects.all(), many=True).data, timeout=300)  # Cache aktualisieren
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = VideoSerializer(video, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            cache.delete('all_videos')  # Cache löschen
            cache.set('all_videos', VideoSerializer(Video.objects.all(), many=True).data, timeout=300)  # Cache aktualisieren
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)