from django.urls import include, path
from .views import VideoConversionProgressView, VideoUploadView, VideoListView, VideoDetailView, VideoClearCache


urlpatterns = [
    path('upload/', VideoUploadView.as_view(), name='video-upload'),
    path('videos/', VideoListView.as_view(), name='video-list'),
    path('videos/<int:pk>/', VideoDetailView.as_view(), name='video-list-detail'),
    path('conversion-progress/<int:video_id>/', VideoConversionProgressView.as_view(), name='conversion-progress'),
    path('django-rq/', include('django_rq.urls')),
    path('clear-cache/', VideoClearCache.as_view(), name='video-clear-cache' ),
]