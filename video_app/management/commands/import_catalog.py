"""
Fill the standard catalog from a folder of sample clips (VFX-62).

Every .mp4 below the folder becomes a standard video (no owner, visible to every
account). The category comes from the file name, e.g. "golf_sports.mp4" -> Sports;
names without a known category become Documentary. Saving the video starts the
thumbnail and conversion jobs like an upload. Videos whose title already exists in
the standard catalog are skipped, so the command can run again after an error.
"""

import re
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from video_app.models import Video

DEFAULT_CATEGORY = "Documentary"
DESCRIPTION = "Sample clip from the Videoflix demo catalog."

# File name word -> category; includes the spelling found in the sample files.
CATEGORY_WORDS = {
    "action": "Action",
    "comedy": "Comedy",
    "crime": "Crime",
    "documentary": "Documentary",
    "mystery": "Mystery",
    "mstery": "Mystery",
    "romance": "Romance",
    "sports": "Sports",
    "sport": "Sports",
}


def title_and_category(path):
    """
    "segelschiff_mstery" -> ("Segelschiff", "Mystery"), "bird_2" -> ("Bird 2", "Documentary").
    """
    words = [w for w in re.split(r"[_\-\s]+", path.stem) if w]
    category = DEFAULT_CATEGORY
    if len(words) > 1 and words[-1].lower() in CATEGORY_WORDS:
        category = CATEGORY_WORDS[words.pop().lower()]
    elif words and words[0].lower() in CATEGORY_WORDS:
        category = CATEGORY_WORDS[words[0].lower()]
    title = " ".join(w[:1].upper() + w[1:] for w in words)
    return title, category


class Command(BaseCommand):
    help = "Import all .mp4 files below a folder as standard videos and start their conversion."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Folder with the sample clips (searched recursively)")

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"Folder not found: {folder}")

        imported = skipped = 0
        for path in sorted(folder.rglob("*.mp4")):
            title, category = title_and_category(path)
            if Video.objects.filter(owner__isnull=True, title=title).exists():
                skipped += 1
                continue
            video = Video(title=title, description=DESCRIPTION, category=category)
            with path.open("rb") as f:
                # Saving copies the file to MEDIA_ROOT/videos and fires the conversion signal.
                video.video_file.save(path.name, File(f), save=True)
            imported += 1
            self.stdout.write(f"Imported {title} ({category})")

        self.stdout.write(f"{imported} imported, {skipped} skipped.")
