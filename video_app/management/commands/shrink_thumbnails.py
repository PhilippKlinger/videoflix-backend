"""
Scale existing thumbnails down to the current thumbnail size (VFX-64).

Thumbnails used to be created in Full HD. The original videos are deleted after the
conversion, so new thumbnails cannot be cut again; instead the existing images are
scaled down in place. Thumbnails that are already small enough stay untouched, so the
command can run again safely.
"""

from django.core.management.base import BaseCommand
from PIL import Image

from video_app.api.utils import THUMBNAIL_SIZE
from video_app.models import Video

# Same visual quality as ffmpeg "-q:v 5" for new thumbnails
JPEG_QUALITY = 80


class Command(BaseCommand):
    help = "Scale existing video thumbnails down to the current thumbnail size."

    def handle(self, *args, **options):
        shrunk = unchanged = missing = 0
        for video in Video.objects.exclude(thumbnail_url="").exclude(thumbnail_url__isnull=True):
            thumbnail = video.thumbnail_url
            if not thumbnail.storage.exists(thumbnail.name):
                self.stdout.write(f"Missing thumbnail for video {video.pk}: {thumbnail.name}")
                missing += 1
                continue
            if self._shrink(thumbnail.path):
                shrunk += 1
            else:
                unchanged += 1
        self.stdout.write(f"{shrunk} shrunk, {unchanged} unchanged, {missing} missing")

    def _shrink(self, path):
        with Image.open(path) as image:
            if image.width <= THUMBNAIL_SIZE[0] and image.height <= THUMBNAIL_SIZE[1]:
                return False
            image.thumbnail(THUMBNAIL_SIZE)
            image.convert("RGB").save(path, "JPEG", quality=JPEG_QUALITY)
        return True
