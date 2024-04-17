from .models import Video
from django.dispatch import receiver
from django.db.models.signals import post_save, post_delete

@receiver(post_save, sender=Video)
def video_post_save(sender, instance, created, **kwargs):
    print(f"Video {instance.title} saved")
    if created:
        print(f"Video {instance.title} uploaded")


@receiver(post_delete, sender=Video)
def video_post_delete(sender, instance, **kwargs):
    print(f"Video {instance.title} deleted")