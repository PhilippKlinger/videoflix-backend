import logging
import subprocess
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from video_app.models import Video, VideoResolution
from .utils import (
    get_base_name_and_extension,
    build_output_filename,
    is_valid_video_extension,
    get_ffmpeg_thumbnail_command,
    get_ffmpeg_convert_command,
)

logger = logging.getLogger(__name__)

RESOLUTIONS = [("120p", 120), ("360p", 360), ("720p", 720), ("1080p", 1080)]


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
            logger.warning("No file attached to the video instance.")
            set_video_failed(video_instance)
            return

        base_name, _ = get_base_name_and_extension(input_file.name)
        output_filename = build_output_filename(base_name, "thumbnail", "jpg")
        input_path = default_storage.path(input_file.name)
        output_path = default_storage.path(output_filename)
        command = get_ffmpeg_thumbnail_command(input_path, output_path)

        subprocess.run(command, check=True)
        with open(output_path, "rb") as f:
            video_instance.thumbnail.save(
                output_filename, ContentFile(f.read()), save=True
            )
        logger.info(f"Thumbnail created and saved to {output_filename}")
        video_instance.save()
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create thumbnail: {e}")
        if video_instance:
            set_video_failed(video_instance)
    except Exception as e:
        logger.error(f"General error in create_thumbnail: {e}")
        if video_instance:
            set_video_failed(video_instance)
    finally:
        if output_filename and default_storage.exists(output_filename):
            default_storage.delete(output_filename)


def convert_video(video_id):
    """
    Convert the original video to multiple resolutions and save each result.
    """
    video_instance = None
    try:
        video_instance = Video.objects.get(id=video_id)
        input_file = video_instance.video_file
        base_name, extension = get_base_name_and_extension(input_file.name)
        if not is_valid_video_extension(extension):
            logger.error("Unsupported video file format.")
            set_video_failed(video_instance)
            return

        total_resolutions = len(RESOLUTIONS)
        for index, (res_label, res_height) in enumerate(RESOLUTIONS):
            output_filename = build_output_filename(base_name, res_label, extension)
            input_path = default_storage.path(input_file.name)
            output_path = default_storage.path(output_filename)
            command = get_ffmpeg_convert_command(input_path, output_path, res_height)

            try:
                subprocess.run(command, check=True)
                VideoResolution.objects.create(
                    original_video=video_instance,
                    resolution=res_label,
                    converted_file=output_filename,
                )
                update_video_progress(
                    video_instance, index + 1, total_resolutions, res_label
                )
                logger.info(f"Video converted and saved to {output_path}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to convert video to {res_label}: {e}")
                set_video_failed(video_instance)
                return
    except Exception as e:
        logger.error(f"General error in convert_video: {e}")
        if video_instance:
            set_video_failed(video_instance)


def set_video_failed(video_instance):
    """
    Set the video status to failed and reset conversion progress.
    """
    video_instance.status = "failed"
    video_instance.conversion_progress = 0
    video_instance.save()


def update_video_progress(video_instance, done, total, current_res):
    """
    Update the progress and current resolution of the video.
    """
    video_instance.conversion_progress = int((done / total) * 100)
    video_instance.current_resolution = current_res
    video_instance.status = "ready"
    video_instance.save()
