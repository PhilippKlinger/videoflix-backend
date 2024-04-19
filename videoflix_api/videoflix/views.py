from django.shortcuts import render
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Video
from .serializers import VideoSerializer
from django.core.cache import cache

class VideoUploadView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        cache_key = 'all_videos'
        serializer = VideoSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            # Cache löschen, um sicherzustellen, dass die VideoListe aktuell bleibt
            cache.delete(cache_key)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


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

