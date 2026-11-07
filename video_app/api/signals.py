"""
Signals for automatic video processing and cleanup on save/delete.
"""

import logging
import os
import posixpath
import shutil
from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.core.files.storage import default_storage
from video_app.models import Video, VideoResolution
from .tasks import convert_video, create_thumbnail
from .utils import STANDARD_VIDEOS_CACHE_KEY
import django_rq

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Video)
def video_post_save(sender, instance, created, **kwargs):
    """
    Enqueue tasks to create thumbnail and convert video after video upload.
    """
    # IDs only: titles and file names are personal data (VFX-31).
    logger.info("Video %s saved", instance.pk)
    if created:
        try:
            logger.info("Video %s uploaded", instance.pk)
            thumbnails_queue = django_rq.get_queue("default", autocommit=True)
            videos_queue = django_rq.get_queue("default", autocommit=True)
            thumbnails_queue.enqueue(create_thumbnail, instance.id)
            videos_queue.enqueue(convert_video, instance.id)
        except Exception as e:
            logger.error("Failed to enqueue tasks for video %s: %s", instance.pk, type(e).__name__)


@receiver(pre_delete, sender=Video)
def video_pre_delete(sender, instance, **kwargs):
    """
    Delete video files and related HLS folders when a video is deleted.
    """
    _delete_original_video_file(instance)
    _delete_own_hls_folder(instance)
    video_dirs = _delete_all_hls_and_collect_dirs(instance)
    _delete_empty_video_dirs(video_dirs)
    _delete_thumbnail_file(instance)
    cache.delete(STANDARD_VIDEOS_CACHE_KEY)

def _delete_original_video_file(instance):
    """
    Delete the original uploaded video file from storage if present.
    """
    if instance.video_file and default_storage.exists(instance.video_file.name):
        default_storage.delete(instance.video_file.name)
        logger.info("Original file of video %s deleted from storage.", instance.pk)


def _delete_own_hls_folder(instance):
    """
    Delete hls/<pk>, which belongs to this video alone, even if no resolution row exists
    (a stopped conversion leaves segments behind). The name is the integer pk, nothing else.
    """
    own_dir = os.path.join(settings.MEDIA_ROOT, "hls", str(int(instance.pk)))
    if os.path.isdir(own_dir) and not os.path.islink(own_dir):
        shutil.rmtree(own_dir, ignore_errors=True)
        logger.info("HLS folder of video %s deleted.", instance.pk)


def _delete_all_hls_and_collect_dirs(instance):
    """
    Delete all HLS folders for every converted resolution and collect their parent directories.
    """
    video_dirs = set()
    for res in instance.resolutions.all():
        if res.converted_file:
            hls_dir = os.path.dirname(res.converted_file.path)
            video_dirs.add(os.path.dirname(hls_dir))
            if _folder_used_by_other_video(res, instance):
                logger.info("HLS folder of video %s (%s) is shared, kept.", instance.pk, res.resolution)
            elif os.path.exists(hls_dir):
                shutil.rmtree(hls_dir, ignore_errors=True)
                logger.info("HLS folder of video %s (%s) deleted.", instance.pk, res.resolution)
    return video_dirs


def _folder_used_by_other_video(res, instance):
    """
    Older videos were converted into a folder named after the file; equal names share one.
    """
    folder = posixpath.dirname(res.converted_file.name)
    return (
        VideoResolution.objects.filter(converted_file__startswith=f"{folder}/")
        .exclude(original_video=instance)
        .exists()
    )


def _delete_empty_video_dirs(video_dirs):
    """
    Delete video directories if they are empty.
    """
    for vid_dir in video_dirs:
        try:
            if os.path.exists(vid_dir) and not os.listdir(vid_dir):
                os.rmdir(vid_dir)
                logger.info("Empty video directory deleted.")
        except Exception as e:
            logger.error("Error deleting a video directory: %s", type(e).__name__)


def _delete_thumbnail_file(instance):
    """
    Delete the thumbnail file from storage if present.
    """
    if instance.thumbnail_url and default_storage.exists(instance.thumbnail_url.name):
        default_storage.delete(instance.thumbnail_url.name)
        logger.info("Thumbnail of video %s deleted from storage.", instance.pk)
