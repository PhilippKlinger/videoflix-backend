"""
Fill the standard catalog from a folder of sample clips (VFX-62).

Every .mp4 below the folder becomes a standard video (no owner, visible to every
account). The category comes from the file name, e.g. "golf_sports.mp4" -> Sports;
names without a known category become Documentary. Saving the video starts the
thumbnail and conversion jobs like an upload. Videos whose title already exists in
the standard catalog are skipped, so the command can run again after an error.

Title, description and category come from catalog.json (key: file name without
extension). Files missing there fall back to the file name and a generic description.
With --replace the standard catalog is deleted (files included) and rebuilt from the
folder; uploads of accounts are never touched. Before the first delete the command checks
that every clip has exactly one catalog entry with valid fields and a unique title, and
that every clip can be read. Without --replace, unknown clips fall back to the file name.
A folder inside MEDIA_ROOT is refused: its files are stored uploads, not sample clips.
"""

import json
import re
import unicodedata
from pathlib import Path

from django.conf import settings
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


def nfc(text):
    """
    File names can arrive decomposed (NFD) from some file systems; compare them composed.
    """
    return unicodedata.normalize("NFC", text)


def title_and_category(path):
    """
    "segelschiff_mstery" -> ("Segelschiff", "Mystery"), "bird_2" -> ("Bird 2", "Documentary").
    """
    words = [w for w in re.split(r"[_\-\s]+", nfc(path.stem)) if w]
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
        media_root = Path(settings.MEDIA_ROOT).resolve()
        if folder.resolve().is_relative_to(media_root):
            raise CommandError(f"{folder} is inside MEDIA_ROOT; use a folder with the sample clips.")
        clips = sorted(folder.rglob("*.mp4"))
        catalog = self._load_catalog()

        if options["replace"]:
            # Everything that can be checked happens before the first delete.
            self._check_replace(clips, catalog, folder)
            self._delete_standard_videos()

        imported = skipped = 0
        for path in clips:
            entry = catalog.get(nfc(path.stem))
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

    def _load_catalog(self):
        try:
            return {nfc(name): entry for name, entry in load_catalog().items()}
        except (OSError, ValueError, AttributeError) as e:
            raise CommandError(f"catalog.json cannot be read: {type(e).__name__}") from e

    def _check_replace(self, clips, catalog, folder):
        """
        Raise CommandError for anything that would leave the catalog incomplete after the delete.
        """
        problems = []
        if not clips:
            problems.append(f"no .mp4 files below {folder}")
        categories = {value for value, _ in Video.CATEGORY_CHOICES}
        for name, entry in catalog.items():
            fields = ("title", "description", "category")
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(f), str) and entry[f].strip() for f in fields
            ):
                problems.append(f"{name}: title, description and category are required")
            elif entry["category"] not in categories:
                problems.append(f"{name}: unknown category {entry['category']}")
        names = [nfc(clip.stem) for clip in clips]
        problems += [f"{n}: clip has no entry in catalog.json" for n in names if n not in catalog]
        problems += [f"{n}: catalog entry has no clip" for n in catalog if n not in names]
        titles = [catalog[n]["title"] for n in names if isinstance(catalog.get(n), dict) and "title" in catalog[n]]
        problems += [f"title used twice: {t}" for t in sorted({t for t in titles if titles.count(t) > 1})]
        for clip in clips:
            try:
                with clip.open("rb") as f:
                    f.read(1)
            except OSError:
                problems.append(f"{clip.name}: cannot be read")
        if problems:
            raise CommandError("Nothing was deleted. " + "; ".join(problems))

    def _delete_standard_videos(self):
        videos = list(Video.objects.filter(owner__isnull=True))
        for video in videos:
            # One by one: the pre_delete signal removes the original, HLS files and thumbnail.
            video.delete()
        self.stdout.write(f"{len(videos)} standard videos deleted.")
