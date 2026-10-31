"""
API endpoints for video management: upload, list, detail, conversion progress, HLS streaming, and cache.
"""

from django.urls import include, path
from .views import (
    VideoConversionProgressView,
    VideoHLSServeView,
    VideoUploadView,
    VideoListView,
    VideoDetailView,
    VideoClearCache,
)

urlpatterns = [
    path("upload/", VideoUploadView.as_view(), name="video-upload"),
    path("video/", VideoListView.as_view(), name="video-list"),
    path("video/<int:pk>/", VideoDetailView.as_view(), name="video-list-detail"),
    path(
        "conversion-progress/<int:video_id>/",
        VideoConversionProgressView.as_view(),
        name="conversion-progress",
    ),
    path(
        "video/<int:video_id>/<str:resolution>/<str:filename>",
        VideoHLSServeView.as_view(),
    ),
    path("django-rq/", include("django_rq.urls")),
    path("clear-cache/", VideoClearCache.as_view(), name="video-clear-cache"),
]
