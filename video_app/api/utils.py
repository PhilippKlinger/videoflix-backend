"""
Utility functions for video processing, file naming, and ffmpeg command generation.
"""

import os


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
