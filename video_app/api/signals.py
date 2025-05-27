import logging
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.core.files.storage import default_storage
from video_app.models import Video
from .tasks import convert_video, create_thumbnail
import django_rq

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Video)
def video_post_save(sender, instance, created, **kwargs):
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
    resolutions = instance.resolutions.all()
    if instance.video_file and default_storage.exists(instance.video_file.name):
        default_storage.delete(instance.video_file.name)
        logger.info(f"Original video file {instance.video_file.name} deleted from storage.")
    for res in resolutions:
        logger.info(f"Checking file: {res.converted_file.name}")
        if res.converted_file and default_storage.exists(res.converted_file.name):
            default_storage.delete(res.converted_file.name)
            logger.info(f"Converted video file {res.converted_file.name} deleted from storage.")
    if instance.thumbnail and default_storage.exists(instance.thumbnail.name):
        default_storage.delete(instance.thumbnail.name)
        logger.info(f"Thumbnail {instance.thumbnail.name} deleted from storage.")
