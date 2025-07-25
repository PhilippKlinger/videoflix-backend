"""
Models for the video_app.

Defines models for Video, VideoResolution, and VideoProgress.
"""

from django.db import models
from django.conf import settings


class Video(models.Model):
    """
    Model representing an uploaded video.
    """

    CATEGORY_CHOICES = [
        ("Action", "Action"),
        ("Comedy", "Comedy"),
        ("Crime", "Crime"),
        ("Documentary", "Documentary"),
        ("Mystery", "Mystery"),
        ("Romance", "Romance"),
        ("Sports", "Sports"),
    ]
    STATUS_CHOICES = [
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=150)
    description = models.CharField(max_length=500)
    category = models.CharField(
        max_length=100, choices=CATEGORY_CHOICES, default=CATEGORY_CHOICES[0][0]
    )
    video_file = models.FileField(upload_to="videos", blank=True, null=True)
    thumbnail_url = models.ImageField(upload_to="thumbnails/", blank=True, null=True)
    conversion_progress = models.IntegerField(default=0)
    current_resolution = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="processing"
    )

    def __str__(self):
        return self.title


class VideoResolution(models.Model):
    """
    Model representing a specific resolution version of a video.
    """

    original_video = models.ForeignKey(
        Video, related_name="resolutions", on_delete=models.CASCADE, null=True
    )
    resolution = models.CharField(max_length=20)
    converted_file = models.FileField(upload_to="videos", max_length=500)

    def __str__(self):
        return f"{self.original_video.title} - {self.resolution}"


class VideoProgress(models.Model):
    """
    Model tracking user progress for video playback.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    video = models.ForeignKey(
        Video, related_name="progresses", on_delete=models.CASCADE
    )
    progress_seconds = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.video.title} ({self.progress_seconds}s)"
