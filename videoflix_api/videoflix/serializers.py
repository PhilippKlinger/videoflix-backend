from rest_framework import serializers
from .models import Video, VideoResolution, Profile
from users.serializers import ProfileSerializer

class VideoResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoResolution
        fields = "__all__"

class VideoSerializer(serializers.ModelSerializer):
    resolutions = VideoResolutionSerializer(many=True, read_only=True)
    favorited_by = serializers.PrimaryKeyRelatedField(queryset=Profile.objects.all(), many=True, required=False)

    class Meta:
        model = Video
        fields = "__all__"

    def create(self, validated_data):
        favorited_by_data = validated_data.pop('favorited_by', [])
        video = Video.objects.create(**validated_data)
        video.favorited_by.set(favorited_by_data)
        return video
