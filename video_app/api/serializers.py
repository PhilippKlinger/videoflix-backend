"""
Serializers for video management and playback tracking.
"""

from rest_framework import serializers
from video_app.models import Video, VideoProgress, VideoResolution
from .utils import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MAX_UPLOAD_SIZE,
    MAX_VIDEO_DURATION_SECONDS,
    probe_video,
)


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
    # Derived from the owner, which the server sets; clients cannot send it.
    visibility = serializers.SerializerMethodField()
    is_own = serializers.SerializerMethodField()

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
            "visibility",
            "is_own",
        ]

    def get_visibility(self, obj):
        """
        Return "public" for standard videos and "private" for uploads.
        """
        return "public" if obj.is_standard else "private"

    def get_is_own(self, obj):
        """
        Return True if the current user uploaded the video.
        """
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.owner_id == request.user.id

    def validate_video_file(self, value):
        """
        Validate type, size and duration of an uploaded video (VFX-27).
        Cheap checks first, ffprobe only for files that pass them.
        """
        ext = value.name.split(".")[-1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise serializers.ValidationError("Only MP4 or MOV files are allowed.")
        if value.size > MAX_UPLOAD_SIZE:
            raise serializers.ValidationError("The file must not be larger than 50 MB.")
        self._validate_video_content(value)
        return value

    def _validate_video_content(self, value):
        """
        Check the real container, a video stream and the duration with ffprobe.
        """
        probe = probe_video(value)
        if not probe or not probe.get("streams"):
            raise serializers.ValidationError("The file is not a readable video.")
        video_format = probe.get("format", {})
        format_names = video_format.get("format_name", "").split(",")
        if not set(format_names) & set(ALLOWED_UPLOAD_EXTENSIONS):
            raise serializers.ValidationError("Only MP4 or MOV files are allowed.")
        try:
            duration = float(video_format["duration"])
        except (KeyError, TypeError, ValueError):
            raise serializers.ValidationError("The file is not a readable video.")
        if duration > MAX_VIDEO_DURATION_SECONDS:
            raise serializers.ValidationError("The video must not be longer than 2 minutes.")

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
