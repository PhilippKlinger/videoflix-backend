"""
Who may see and change which videos (VFX-26).

Standard videos have no owner and are visible to every logged-in account.
Uploads belong to their owner; only the owner and admins can see or change them.
"""

from django.db.models import Q

from accounts_app.api.utils import is_admin
from video_app.models import Video


def visible_videos(user):
    """
    Return the videos the user may see.
    """
    if is_admin(user):
        return Video.objects.all()
    return Video.objects.filter(Q(owner__isnull=True) | Q(owner=user))


def can_change_video(user, video):
    """
    Return True if the user may change or delete the video.
    """
    return is_admin(user) or video.owner_id == user.id
