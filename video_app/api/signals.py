"""
Signals for automatic video processing and cleanup on save/delete.
"""

import logging
import os
import shutil
from django.core.cache import cache
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.core.files.storage import default_storage
from video_app.models import Video
from .tasks import convert_video, create_thumbnail
import django_rq

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Video)
def video_post_save(sender, instance, created, **kwargs):
    """
    Enqueue tasks to create thumbnail and convert video after video upload.
    """
    logger.info(f"Video {instance.title} saved")
    if created:
        try:
            logger.info(f"Video {instance.title} uploaded")
            thumbnails_queue = django_rq.get_queue("default", autocommit=True)
            videos_queue = django_rq.get_queue("default", autocommit=True)
            thumbnails_queue.enqueue(create_thumbnail, instance.id)
            videos_queue.enqueue(convert_video, instance.id)
        except Exception as e:
            logger.error(f"Failed to enqueue tasks: {e}")


@receiver(pre_delete, sender=Video)
def video_pre_delete(sender, instance, **kwargs):
    """
    Delete video files and related HLS folders when a video is deleted.
    """
    _delete_original_video_file(instance)
    video_dirs = _delete_all_hls_and_collect_dirs(instance)
    _delete_empty_video_dirs(video_dirs)
    _delete_thumbnail_file(instance)
    cache.delete("all_videos")

def _delete_original_video_file(instance):
    """
    Delete the original uploaded video file from storage if present.
    """
    if instance.video_file and default_storage.exists(instance.video_file.name):
        default_storage.delete(instance.video_file.name)
        logger.info(
            f"Original video file {instance.video_file.name} deleted from storage."
        )


def _delete_all_hls_and_collect_dirs(instance):
    """
    Delete all HLS folders for every converted resolution and collect their parent directories.
    """
    video_dirs = set()
    for res in instance.resolutions.all():
        if res.converted_file:
            hls_dir = os.path.dirname(res.converted_file.path)
            video_dirs.add(os.path.dirname(hls_dir))
            if os.path.exists(hls_dir):
                shutil.rmtree(hls_dir, ignore_errors=True)
                logger.info(f"HLS folder {hls_dir} deleted.")
    return video_dirs


def _delete_empty_video_dirs(video_dirs):
    """
    Delete video directories if they are empty.
    """
    for vid_dir in video_dirs:
        try:
            if os.path.exists(vid_dir) and not os.listdir(vid_dir):
                os.rmdir(vid_dir)
                logger.info(f"Video directory {vid_dir} deleted (was empty).")
        except Exception as e:
            logger.error(f"Error deleting video directory {vid_dir}: {e}")


def _delete_thumbnail_file(instance):
    """
    Delete the thumbnail file from storage if present.
    """
    if instance.thumbnail_url and default_storage.exists(instance.thumbnail_url.name):
        default_storage.delete(instance.thumbnail_url.name)
        logger.info(f"Thumbnail {instance.thumbnail_url.name} deleted from storage.")
