
from rq import Retry
from .models import Video
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_delete
from django.core.files.storage import default_storage
from .tasks import convert_video, create_thumbnail
import django_rq


@receiver(post_save, sender=Video)
def video_post_save(sender, instance, created, **kwargs):
    print(f"Video {instance.title} saved")
    if created:
        print(f"Video {instance.title} uploaded")
        create_thumbnail(instance)
        videos_queue = django_rq.get_queue('default', autocommit=True)
        videos_queue.enqueue(convert_video, instance)
        
        
        
        


@receiver(pre_delete, sender=Video)
def video_pre_delete(sender, instance, **kwargs):
    resolutions = instance.resolutions.all()
    
    if instance.video_file and default_storage.exists(instance.video_file.name):
        default_storage.delete(instance.video_file.name)
        print(f"Original video file {instance.video_file.name} deleted from storage.")
        
    for res in resolutions:
        print(f"Checking file: {res.converted_file.name}")
        if res.converted_file and default_storage.exists(res.converted_file.name):
            default_storage.delete(res.converted_file.name)
            print(f"Converted video file {res.converted_file.name} deleted from storage.")
            
    if instance.thumbnail and default_storage.exists(instance.thumbnail.name):
        default_storage.delete(instance.thumbnail.name)
        print(f"Thumbnail {instance.thumbnail.name} deleted from storage.")