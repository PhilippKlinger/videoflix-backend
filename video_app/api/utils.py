"""
Utility functions for video processing, file naming, and ffmpeg command generation.
"""

import json
import os
import subprocess
import tempfile
from fractions import Fraction

# Only standard videos are cached; private uploads never enter the shared cache.
STANDARD_VIDEOS_CACHE_KEY = "all_videos"

# Upload limits (masterplan G6).
ALLOWED_UPLOAD_EXTENSIONS = ["mp4", "mov"]
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VIDEO_DURATION_SECONDS = 120
MAX_UPLOADS_PER_ACCOUNT = 2
MAX_VIDEO_PIXELS = 8294400  # 3840 x 2160
MAX_VIDEO_FPS = 120
# Per resolution, for uploads only: a small file with many frames must not block the single worker.
UPLOAD_CONVERSION_TIMEOUT_SECONDS = 300

# Uploads are untrusted: a crafted file must not make ffmpeg or ffprobe open URLs.
LOCAL_FILES_ONLY = ["-protocol_whitelist", "file"]
# Nor may a small file claim many GB of memory with huge frames. An input option, so it goes before -i.
PIXEL_LIMIT = ["-max_pixels", str(MAX_VIDEO_PIXELS)]


def get_base_name_and_extension(file_name):
    """
    Return base name and file extension for a given filename.
    Example: "video.mp4" -> ("video", "mp4")
    """
    if "." in file_name:
        base = file_name.rsplit(".", 1)[0]
        ext = file_name.split(".")[-1]
        return base, ext
    return file_name, None


def build_output_filename(base_name, suffix, extension):
    """
    Build a new filename with suffix and extension.
    Example: ("video", "720p", "mp4") -> "video_720p.mp4"
    """
    return f"{base_name}_{suffix}.{extension}"


def is_valid_video_extension(extension):
    """
    Check if the file extension is a supported video format.
    """
    return extension and extension.lower() in ["mp4", "mov", "avi", "mkv"]


# Tiles show the thumbnail at about 533x300 and the hero is wider; 1280x720 stays sharp
# for both on 2x screens while keeping files small (VFX-64).
THUMBNAIL_SIZE = (1280, 720)
THUMBNAIL_QUALITY = "5"
# Clips shorter than this seek position produce no frame; tasks retry from the start.
THUMBNAIL_SEEK = "00:00:05"
THUMBNAIL_SEEK_FALLBACK = "00:00:00"


def get_ffmpeg_thumbnail_command(input_path, output_path, seek=THUMBNAIL_SEEK):
    """
    Return the ffmpeg command for creating a video thumbnail image at the given seek position.
    """
    return [
        "ffmpeg",
        # A failed first attempt can leave an output file; never wait for an overwrite prompt.
        "-y",
        *LOCAL_FILES_ONLY,
        *PIXEL_LIMIT,
        "-i",
        input_path,
        "-ss",
        seek,
        "-vframes",
        "1",
        "-q:v",
        THUMBNAIL_QUALITY,
        "-s",
        f"{THUMBNAIL_SIZE[0]}x{THUMBNAIL_SIZE[1]}",
        output_path,
    ]


# Short segments let playback start sooner; keyframes at fixed times keep every segment this long.
HLS_SEGMENT_SECONDS = 4


def get_ffmpeg_hls_command(input_path, output_dir, base_name, height, max_seconds=None):
    """
    Return the ffmpeg command and output path for creating HLS segments and playlist.

    With max_seconds the output is cut off there, whatever length the file header claims.
    """
    playlist_path = os.path.join(output_dir, "index.m3u8")
    segment_path = os.path.join(output_dir, "index%03d.ts")
    # An output option, so it goes after -i.
    limit = ["-t", str(max_seconds)] if max_seconds else []
    return [
        "ffmpeg",
        *LOCAL_FILES_ONLY,
        *PIXEL_LIMIT,
        "-i",
        input_path,
        *limit,
        "-fpsmax",
        "60",
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{HLS_SEGMENT_SECONDS})",
        "-sc_threshold",
        "0",
        "-hls_time",
        str(HLS_SEGMENT_SECONDS),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        segment_path,
        "-f",
        "hls",
        playlist_path,
    ], playlist_path


def get_ffprobe_command(input_path):
    """
    Return the ffprobe command that reads container format, duration and video streams.
    """
    return [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate:format=format_name,duration",
        "-of",
        "json",
        *LOCAL_FILES_ONLY,
        *PIXEL_LIMIT,
        input_path,
    ]


def parse_frame_rate(value):
    """
    Return the frame rate from ffprobe's "num/den" text, or 0 if it is unknown (0/0, malformed).
    """
    try:
        return Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return 0


def probe_video(uploaded_file):
    """
    Read an uploaded file with ffprobe.
    Return the parsed result, or None if ffprobe cannot read the file.
    """
    # Large uploads are already on disk; small ones are only in memory.
    if hasattr(uploaded_file, "temporary_file_path"):
        return _run_ffprobe(uploaded_file.temporary_file_path())
    _, extension = get_base_name_and_extension(uploaded_file.name)
    with tempfile.NamedTemporaryFile(suffix=f".{extension}", delete=False) as temp_file:
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
    try:
        return _run_ffprobe(temp_file.name)
    finally:
        os.remove(temp_file.name)
        uploaded_file.seek(0)


def _run_ffprobe(input_path):
    """
    Run ffprobe and return its JSON output, or None on any error.
    """
    try:
        result = subprocess.run(
            get_ffprobe_command(input_path), capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
