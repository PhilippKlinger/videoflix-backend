from rest_framework import serializers
from video_app.models import Video, VideoResolution


class VideoResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoResolution
        fields = ["id", "original_video", "resolution", "converted_file"]


class VideoSerializer(serializers.ModelSerializer):
    resolutions = VideoResolutionSerializer(many=True, read_only=True)
    thumbnail_url = serializers.SerializerMethodField()

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
        ]

    def validate_video_file(self, value):
        ext = value.name.split(".")[-1].lower()
        if ext not in ["mp4", "mov", "avi", "mkv"]:
            raise serializers.ValidationError("Unsupported file type.")
        return value

    def validate(self, data):
        # Wenn PATCH (partial update), keine Pflichtfeldprüfung
        if getattr(self, "partial", False):
            return data
        # Pflichtfelder prüfen (POST/PUT)
        required_fields = ["title", "description", "genre", "category", "video_file"]
        errors = {}
        for field in required_fields:
            if field not in data or not data.get(field):
                errors[field] = "This field is required."
        if errors:
            raise serializers.ValidationError(errors)
        return data

    def get_thumbnail_url(self, obj):
        request = self.context.get("request")
        if obj.thumbnail_url:
            url = obj.thumbnail_url.url
            if request is not None:
                return request.build_absolute_uri(url)
            else:
                # Fallback, wenn kein Request-Objekt vorhanden ist
                return url
        return None
