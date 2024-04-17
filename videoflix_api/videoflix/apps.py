from django.apps import AppConfig


class VideoflixConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'videoflix'

    def ready(self):
        from . import signals