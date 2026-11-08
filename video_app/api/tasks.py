"""
Background tasks for video processing: thumbnail creation, conversion, and caching.
"""

import logging
import os
import subprocess
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from video_app.models import Video, VideoResolution
from django.core.cache import cache
from .utils import (
    get_base_name_and_extension,
    build_output_filename,
    get_ffmpeg_hls_command,
    is_valid_video_extension,
    MAX_VIDEO_DURATION_SECONDS,
    STANDARD_VIDEOS_CACHE_KEY,
    UPLOAD_CONVERSION_TIMEOUT_SECONDS,
    get_ffmpeg_thumbnail_command,
    THUMBNAIL_SEEK_FALLBACK,
)

logger = logging.getLogger(__name__)

RESOLUTIONS = [("480p", 480), ("720p", 720), ("1080p", 1080)]


def create_thumbnail(video_id):
    """
    Create and save a thumbnail image for a given video.
    """
    video_instance = None
    output_filename = None
    try:
        video_instance = Video.objects.get(id=video_id)
        input_file = video_instance.video_file
        if not input_file:
            logger.warning("Video %s has no file attached.", video_id)
            set_video_failed(video_instance)
            return

        base_name, _ = get_base_name_and_extension(input_file.name)
        output_filename = build_output_filename(base_name, "thumbnail", "jpg")
        input_path = default_storage.path(input_file.name)
        output_path = default_storage.path(output_filename)

        _run_thumbnail_command(input_path, output_path, video_instance, output_filename)
    except Exception as e:
        logger.error("General error in create_thumbnail for video %s: %s", video_id, type(e).__name__)
        if video_instance:
            set_video_failed(video_instance)
    finally:
        _cleanup_temp_thumbnail(output_filename)


def _run_thumbnail_command(input_path, output_path, video_instance, output_filename):
    """
    Run ffmpeg to create a thumbnail and save it to the video instance.
    """
    if not _create_thumbnail_file(input_path, output_path, video_instance):
        logger.error("Failed to create thumbnail for video %s.", video_instance.pk)
        set_video_failed(video_instance)
        return
    with open(output_path, "rb") as f:
        video_instance.thumbnail_url.save(
            output_filename, ContentFile(f.read()), save=False
        )
    # update() instead of save(): a video deleted meanwhile must not be inserted again.
    saved = Video.objects.filter(pk=video_instance.pk).update(
        thumbnail_url=video_instance.thumbnail_url.name
    )
    if not saved:
        video_instance.thumbnail_url.delete(save=False)
        return
    logger.info("Thumbnail created for video %s.", video_instance.pk)


def _create_thumbnail_file(input_path, output_path, video_instance):
    """
    Seek to the default position; if that yields no image (clip too short), retry from the first frame.
    Return True if a thumbnail file was written.
    """
    if _run_thumbnail_attempt(get_ffmpeg_thumbnail_command(input_path, output_path), output_path):
        return True
    logger.info("No thumbnail at the default position for video %s, retrying from the first frame.", video_instance.pk)
    command = get_ffmpeg_thumbnail_command(input_path, output_path, seek=THUMBNAIL_SEEK_FALLBACK)
    return _run_thumbnail_attempt(command, output_path)


def _run_thumbnail_attempt(command, output_path):
    """
    An attempt succeeds only if ffmpeg exits 0 and leaves a non-empty file; some builds exit 0 without output.
    """
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.info("ffmpeg exited with code %s while creating a thumbnail.", e.returncode)
        return False
    return _thumbnail_written(output_path)


def _thumbnail_written(output_path):
    return os.path.isfile(output_path) and os.path.getsize(output_path) > 0


def _cleanup_temp_thumbnail(output_filename):
    """
    Remove the temporary thumbnail file if it exists.
    """
    if output_filename and default_storage.exists(output_filename):
        default_storage.delete(output_filename)


def convert_video(video_id):
    """
    Convert the original video to multiple resolutions and save each result.
    """
    video_instance = Video.objects.get(id=video_id)
    input_file = video_instance.video_file
    base_name, extension = get_base_name_and_extension(input_file.name)
    if not is_valid_video_extension(extension):
        set_video_failed(video_instance)
        return

    # One folder per video: equal file names must never share or overwrite streams.
    output_base_dir = os.path.join(settings.MEDIA_ROOT, "hls", str(video_instance.pk))
    os.makedirs(output_base_dir, exist_ok=True)
    if _convert_to_all_resolutions(video_instance, input_file, base_name, output_base_dir):
        _delete_original_video(video_instance)


def _delete_original_video(video_instance):
    """
    Delete the uploaded original once every resolution exists; only HLS is streamed (VFX-30).
    """
    video_instance.video_file.delete(save=False)
    # update() instead of save(), so a thumbnail saved meanwhile is not overwritten.
    Video.objects.filter(pk=video_instance.pk).update(video_file=None)


def _convert_to_all_resolutions(video_instance, input_file, base_name, output_base_dir):
    """
    Loop through all target resolutions, convert and save each one.
    Return True if every resolution was converted.
    """
    total_resolutions = len(RESOLUTIONS)
    all_converted = True
    for index, (res_label, res_height) in enumerate(RESOLUTIONS):
        out_dir = os.path.join(output_base_dir, res_label)
        os.makedirs(out_dir, exist_ok=True)
        converted = _run_hls_command(
            video_instance,
            input_file,
            out_dir,
            base_name,
            res_label,
            res_height,
            index + 1,
            total_resolutions,
        )
        all_converted = all_converted and converted
        # An upload that failed once does not hold the single worker any longer.
        if not converted and video_instance.owner_id:
            break
    return all_converted


def _run_hls_command(
    video_instance,
    input_file,
    out_dir,
    base_name,
    res_label,
    res_height,
    progress_index,
    total_resolutions,
):
    """
    Run ffmpeg to create HLS for a resolution, update DB and cache.
    Return True on success.
    """
    # Uploads are capped at the allowed length and time; catalog videos (no owner) are not.
    max_seconds = MAX_VIDEO_DURATION_SECONDS if video_instance.owner_id else None
    timeout = UPLOAD_CONVERSION_TIMEOUT_SECONDS if video_instance.owner_id else None
    command, playlist_path = get_ffmpeg_hls_command(
        default_storage.path(input_file.name), out_dir, base_name, res_height, max_seconds
    )
    try:
        # ffmpeg prints file names and metadata (titles, GPS); logs carry IDs only (VFX-31).
        subprocess.run(
            command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout
        )
        rel_playlist_path = os.path.relpath(
            os.path.join(out_dir, "index.m3u8"), settings.MEDIA_ROOT
        )
        VideoResolution.objects.create(
            original_video=video_instance,
            resolution=res_label,
            converted_file=rel_playlist_path,
        )
        update_video_progress(
            video_instance, progress_index, total_resolutions, res_label
        )
        update_video_cache()
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            "ffmpeg exited with code %s while converting video %s to %s.",
            e.returncode, video_instance.pk, res_label,
        )
    except Exception as e:
        logger.error("Error converting video %s to %s: %s", video_instance.pk, res_label, type(e).__name__)
    set_video_failed(video_instance)
    return False


def set_video_failed(video_instance):
    """
    Set the video status to failed and reset conversion progress.
    """
    video_instance.status = "failed"
    video_instance.conversion_progress = 0
    Video.objects.filter(pk=video_instance.pk).update(status="failed", conversion_progress=0)


def update_video_progress(video_instance, done, total, current_res):
    """
    Update the progress and current resolution of the video.
    """
    video_instance.conversion_progress = int((done / total) * 100)
    video_instance.current_resolution = current_res
    video_instance.status = "ready"
    # update() instead of save(): a video deleted meanwhile must not be inserted again.
    Video.objects.filter(pk=video_instance.pk).update(
        conversion_progress=video_instance.conversion_progress,
        current_resolution=current_res,
        status="ready",
    )


def update_video_cache():
    """
    Delete the video list cache to trigger refresh.
    """
    cache.delete(STANDARD_VIDEOS_CACHE_KEY)
