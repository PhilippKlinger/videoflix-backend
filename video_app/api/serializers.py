"""
Serializers for video management and playback tracking.
"""

from rest_framework import serializers
from video_app.models import Video, VideoProgress, VideoResolution


class VideoResolutionSerializer(serializers.ModelSerializer):
    """
    Serializer for VideoResolution model.
    """

    class Meta:
        model = VideoResolution
        fields = ["id", "original_video", "resolution", "converted_file"]


class VideoSerializer(serializers.ModelSerializer):
    """
    Serializer for Video model, including resolutions and user progress.
    """

    resolutions = VideoResolutionSerializer(many=True, read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    user_progress = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            "id",
            "created_at",
            "title",
            "description",
            "category",
            "video_file",
            "thumbnail_url",
            "conversion_progress",
            "current_resolution",
            "resolutions",
            "user_progress",
        ]

    def validate_video_file(self, value):
        """
        Validate that the uploaded file is a supported video format.
        """
        ext = value.name.split(".")[-1].lower()
        if ext not in ["mp4", "mov", "avi", "mkv"]:
            raise serializers.ValidationError("Unsupported file type.")
        return value

    def validate(self, data):
        """
        Validate required fields for video creation (skip on PATCH).
        """
        if getattr(self, "partial", False):
            return data
        required_fields = ["title", "description", "category", "video_file"]
        errors = {}
        for field in required_fields:
            if field not in data or not data.get(field):
                errors[field] = "This field is required."
        if errors:
            raise serializers.ValidationError(errors)
        return data

    def get_thumbnail_url(self, obj):
        """
        Return absolute URL for the thumbnail image.
        """
        request = self.context.get("request")
        if obj.thumbnail_url:
            url = obj.thumbnail_url.url
            if request is not None:
                return request.build_absolute_uri(url)
            else:
                return url
        return None

    def get_user_progress(self, obj):
        """
        Return playback progress in seconds for the current user.
        """
        request = self.context.get("request", None)
        if not request or not request.user.is_authenticated:
            return None
        try:
            progress = VideoProgress.objects.get(user=request.user, video=obj)
            return progress.progress_seconds
        except VideoProgress.DoesNotExist:
            return 0
