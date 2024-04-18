from django.shortcuts import render
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authtoken.models import Token
from .models import Video
from .serializers import VideoSerializer, UserRegistrationSerializer, LoginSerializer
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

class RegisterUserView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, created = Token.objects.get_or_create(user=user)
            return Response(
                {"user_id": user.pk, "email": user.email, "token": token.key},
                status=status.HTTP_201_CREATED,
            )
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginUserView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data
            token, created = Token.objects.get_or_create(user=user)
            return Response(
                {"token": token.key, "user_id": user.pk, "username": user.username},
                status=status.HTTP_200_OK,
            )
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
