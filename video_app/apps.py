from django.apps import AppConfig


class VideoflixConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'video_app'

    def ready(self):
        from .api import signals