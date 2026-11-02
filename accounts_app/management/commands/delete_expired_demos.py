"""
Hard-delete demo accounts older than their lifetime (VFX-28, masterplan G5).

Uploads are deleted with the account (CASCADE); the video pre_delete signal
removes their files. Meant to run regularly, e.g. every 10 minutes.
"""

from django.core.management.base import BaseCommand

from accounts_app.models import CustomUser


class Command(BaseCommand):
    help = "Delete expired demo accounts together with their uploads and files."

    def handle(self, *args, **options):
        expired = CustomUser.objects.expired_demos()
        count = expired.count()
        expired.delete()
        self.stdout.write(f"Deleted {count} expired demo account(s).")
