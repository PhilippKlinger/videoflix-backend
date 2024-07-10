from rest_framework import serializers
from .models import Video, VideoResolution
from users.serializers import ProfileSerializer

class VideoResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoResolution
        fields = "__all__"

class VideoSerializer(serializers.ModelSerializer):
    resolutions = VideoResolutionSerializer(many=True, read_only=True)
    favorited_by = ProfileSerializer(many=True, read_only=True)

    class Meta:
        model = Video
        fields = "__all__"
