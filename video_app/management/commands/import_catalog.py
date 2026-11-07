"""
Fill the standard catalog from a folder of sample clips (VFX-62).

Every .mp4 below the folder becomes a standard video (no owner, visible to every
account). The category comes from the file name, e.g. "golf_sports.mp4" -> Sports;
names without a known category become Documentary. Saving the video starts the
thumbnail and conversion jobs like an upload. Videos whose title already exists in
the standard catalog are skipped, so the command can run again after an error.

Title, description and category come from catalog.json (key: file name without
extension). Files missing there fall back to the file name and a generic description.
With --replace the standard catalog is deleted first (files included) and rebuilt from
the folder; uploads of accounts are never touched.
"""

import json
import re
from pathlib import Path

from django.core.cache import cache
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from video_app.api.utils import STANDARD_VIDEOS_CACHE_KEY
from video_app.models import Video

CATALOG_PATH = Path(__file__).with_name("catalog.json")
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


def load_catalog():
    """
    Read the curated texts: {"file_name_without_extension": {"title", "description", "category"}}.
    """
    with CATALOG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


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
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete all standard videos and their files first, then import the folder",
        )

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"Folder not found: {folder}")
        clips = sorted(folder.rglob("*.mp4"))

        if options["replace"]:
            # Check first, delete afterwards: a wrong folder must never empty the catalog.
            if not clips:
                raise CommandError(f"No .mp4 files below {folder}; nothing was deleted.")
            self._delete_standard_videos(clips)

        catalog = load_catalog()
        imported = skipped = 0
        for path in clips:
            entry = catalog.get(path.stem)
            if entry:
                title, description, category = entry["title"], entry["description"], entry["category"]
            else:
                title, category = title_and_category(path)
                description = DESCRIPTION
                self.stderr.write(self.style.WARNING(f"{path.stem} is not in catalog.json, using the file name"))
            if Video.objects.filter(owner__isnull=True, title=title).exists():
                skipped += 1
                continue
            video = Video(title=title, description=description, category=category)
            with path.open("rb") as f:
                # Saving copies the file to MEDIA_ROOT/videos and fires the conversion signal.
                video.video_file.save(path.name, File(f), save=True)
            imported += 1
            self.stdout.write(f"Imported {title} ({category})")

        # New videos do not clear the list cache by themselves.
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        self.stdout.write(f"{imported} imported, {skipped} skipped.")

    def _delete_standard_videos(self, clips):
        videos = list(Video.objects.filter(owner__isnull=True))
        # The stored copies are deleted with the videos; a folder holding them would lose its clips.
        stored = {Path(v.video_file.path).resolve() for v in videos if v.video_file}
        if stored & {clip.resolve() for clip in clips}:
            raise CommandError("The folder contains files of the current catalog; nothing was deleted.")
        for video in videos:
            # One by one: the pre_delete signal removes the original, HLS files and thumbnail.
            video.delete()
        self.stdout.write(f"{len(videos)} standard videos deleted.")
