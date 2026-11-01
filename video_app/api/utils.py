"""
Utility functions for video processing, file naming, and ffmpeg command generation.
"""

import json
import os
import subprocess
import tempfile

# Only standard videos are cached; private uploads never enter the shared cache.
STANDARD_VIDEOS_CACHE_KEY = "all_videos"

# Upload limits (masterplan G6).
ALLOWED_UPLOAD_EXTENSIONS = ["mp4", "mov"]
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VIDEO_DURATION_SECONDS = 120
MAX_UPLOADS_PER_ACCOUNT = 2


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


def get_ffmpeg_thumbnail_command(input_path, output_path):
    """
    Return the ffmpeg command for creating a video thumbnail image.
    """
    return [
        "ffmpeg",
        "-i",
        input_path,
        "-ss",
        "00:00:05",
        "-vframes",
        "1",
        "-q:v",
        "2",
        "-s",
        "1920x1080",
        output_path,
    ]


def get_ffmpeg_hls_command(input_path, output_dir, base_name, height):
    """
    Return the ffmpeg command and output path for creating HLS segments and playlist.
    """
    playlist_path = os.path.join(output_dir, "index.m3u8")
    segment_path = os.path.join(output_dir, "index%03d.ts")
    return [
        "ffmpeg",
        "-i",
        input_path,
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "32",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-hls_time",
        "16",
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
        "stream=codec_type:format=format_name,duration",
        "-of",
        "json",
        input_path,
    ]


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
