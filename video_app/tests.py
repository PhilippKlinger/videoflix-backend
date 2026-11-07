import importlib
import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from io import StringIO
from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve, reverse
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from video_app.models import Video, VideoResolution, VideoProgress
from accounts_app.models import CustomUser
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import mock_open, patch, MagicMock
from video_app.api.tasks import convert_video
from video_app.api.views import _get_hls_file_path
from video_app.api import tasks, utils
from video_app.api.utils import STANDARD_VIDEOS_CACHE_KEY

# Admin pages need static files; the manifest only exists after collectstatic (prod image).
PLAIN_STATIC_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

MP4_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"


def ffprobe_output(format_name=MP4_FORMAT, duration="30.0", has_video=True, returncode=0):
    """
    Fake result of the ffprobe process; tests replace only the process call.
    """
    data = {
        "streams": [{"codec_type": "video"}] if has_video else [],
        "format": {"format_name": format_name, "duration": duration},
    }
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=returncode, stdout=json.dumps(data), stderr=""
    )


class HlsCommandTests(SimpleTestCase):
    def test_segments_are_four_seconds_with_fixed_keyframes(self):
        # Short segments start faster; keyframes at fixed times keep every segment 4 s long.
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "base", 720)
        self.assertEqual(cmd[cmd.index("-hls_time") + 1], "4")
        self.assertEqual(
            cmd[cmd.index("-force_key_frames") + 1], "expr:gte(t,n_forced*4)"
        )
        self.assertEqual(cmd[cmd.index("-sc_threshold") + 1], "0")

    def test_encoder_uses_a_slower_preset_for_smaller_segments(self):
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "base", 720)
        self.assertEqual(cmd[cmd.index("-preset") + 1], "veryfast")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "28")

    def test_no_length_limit_by_default(self):
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "base", 720)
        self.assertNotIn("-t", cmd)

    def test_length_limit_is_an_output_option_after_the_input(self):
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "base", 720, max_seconds=120)
        self.assertEqual(cmd[cmd.index("-t") + 1], "120")
        self.assertGreater(cmd.index("-t"), cmd.index("-i") + 1)
        # Every other argument stays as it is.
        plain, _ = utils.get_ffmpeg_hls_command("input", "output", "base", 720)
        limited = list(cmd)
        del limited[cmd.index("-t") : cmd.index("-t") + 2]
        self.assertEqual(limited, plain)


class FfmpegOutputTests(APITestCase):
    """
    ffmpeg prints file names and metadata (titles, GPS) to stderr, which goes straight to the
    host journal. Its output is discarded; logs carry only the exit code and IDs (X5 finding 1).
    """

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        queue = patch("video_app.api.signals.django_rq.get_queue")
        queue.start()
        self.addCleanup(queue.stop)
        self.video = Video.objects.create(
            title="Private Holiday", description="d", category="Comedy",
            video_file="videos/max_mustermann.mp4",
        )

    def _assert_output_discarded(self, mock_run):
        self.assertTrue(mock_run.called)
        for call in mock_run.call_args_list:
            self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)

    @patch("video_app.api.tasks.subprocess.run")
    def test_conversion_discards_ffmpeg_output(self, mock_run):
        convert_video(self.video.pk)
        self._assert_output_discarded(mock_run)

    @patch("video_app.api.tasks.subprocess.run")
    def test_thumbnail_discards_ffmpeg_output(self, mock_run):
        tasks.create_thumbnail(self.video.pk)
        self._assert_output_discarded(mock_run)

    @patch("video_app.api.tasks.subprocess.run")
    def test_failed_conversion_logs_exit_code_and_id_only(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(69, ["ffmpeg", "videos/max_mustermann.mp4"])
        with self.assertLogs("video_app.api.tasks", level="ERROR") as logs:
            convert_video(self.video.pk)
        output = " ".join(logs.output)
        self.assertIn("69", output)
        self.assertIn(str(self.video.pk), output)
        self.assertNotIn("mustermann", output)
        self.assertNotIn("Private Holiday", output)


class VideoAppIntegrationTests(APITestCase):
    """
    Integration tests for the video_app.

    Covers upload, listing, detail, patch, conversion, progress, cache, resolutions, and HLS streaming.
    """

    def setUp(self):
        # Throttle counters live in the cache (VFX-29); each test starts fresh.
        cache.clear()
        self.unauthenticated_client = APIClient()
        self.raw_password = "testpw"
        self.user = CustomUser.objects.create_user(
            username="video_user",
            email="video@test.de",
            password=self.raw_password,
            is_active=True,
        )

        login_data = {"email": self.user.email, "password": self.raw_password}
        response = self.client.post("/api/login/", login_data, format="json")

        print("LOGIN RESPONSE:", response.data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.cookies)

        # Sample video file (dummy, not playable but valid for upload)
        self.video_file = SimpleUploadedFile(
            "test.mp4",
            b"fake content",  # not a real video but fine for DB tests
            content_type="video/mp4",
        )

        # Admin client for actions that only admins may perform (VFX-26)
        self.admin = CustomUser.objects.create_user(
            username="video_admin",
            email="video-admin@test.de",
            password=self.raw_password,
            is_active=True,
            is_staff=True,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin)

        # Another video for listing
        self.other_video = Video.objects.create(
            title="Second Video",
            description="Another test video.",
            category="Action",
            video_file=self.video_file,
        )

    # --- UPLOAD ---
    @patch("video_app.api.utils.subprocess.run", return_value=ffprobe_output())
    def test_upload_video_success(self, mock_run):
        url = "/api/upload/"
        video_file = SimpleUploadedFile(
            "test.mp4", b"fake content", content_type="video/mp4"
        )
        data = {
            "title": "Test Video",
            "description": "A video for testing.",
            "category": "Comedy",
            "video_file": video_file,
        }
        response = self.client.post(url, data, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Video.objects.filter(title="Test Video").exists())

    def test_upload_video_missing_fields(self):
        url = "/api/upload/"
        data = {
            "title": "No File",
            "description": "Missing video file.",
            "category": "Comedy",
        }
        response = self.client.post(url, data, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", str(response.data))

    def test_upload_video_no_auth(self):
        url = "/api/upload/"
        data = {
            "title": "No Auth Video",
            "description": "No token provided.",
            "category": "Comedy",
            "video_file": self.video_file,
        }
        response = self.unauthenticated_client.post(url, data, format="multipart")
        self.assertEqual(response.status_code, 401)

    # --- VIDEO LIST ---
    def test_list_videos_authenticated(self):
        url = "/api/video/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(isinstance(response.data, list))
        self.assertGreaterEqual(len(response.data), 1)
        # Should be in descending order by created_at
        created_at_list = [v["created_at"] for v in response.data]
        self.assertEqual(created_at_list, sorted(created_at_list, reverse=True))

    def test_list_videos_no_auth(self):
        url = "/api/video/"
        response = self.unauthenticated_client.get(url)
        self.assertEqual(response.status_code, 401)

    # --- VIDEO DETAIL ---
    def test_get_video_detail_success(self):
        url = f"/api/video/{self.other_video.pk}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Second Video")

    def test_get_video_detail_invalid_id(self):
        url = "/api/video/99999/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_get_video_detail_no_auth(self):
        url = f"/api/video/{self.other_video.pk}/"
        response = self.unauthenticated_client.get(url)
        self.assertEqual(response.status_code, 401)

    # --- PATCH/UPDATE VIDEO ---
    def test_patch_video_success(self):
        url = f"/api/video/{self.other_video.pk}/"
        data = {"description": "Updated description"}
        response = self.admin_client.patch(url, data)
        self.assertEqual(response.status_code, 200)
        self.other_video.refresh_from_db()
        self.assertEqual(self.other_video.description, "Updated description")

    def test_patch_video_invalid_id(self):
        url = "/api/video/99999/"
        data = {"description": "Does not exist"}
        response = self.client.patch(url, data)
        self.assertEqual(response.status_code, 404)

    def test_patch_video_invalid_data(self):
        url = f"/api/video/{self.other_video.pk}/"
        response = self.admin_client.patch(url, {"category": ""})
        self.assertEqual(response.status_code, 200)
        self.other_video.refresh_from_db()
        self.assertIn(self.other_video.category, ["", "Action"])

    def test_patch_video_no_auth(self):
        url = f"/api/video/{self.other_video.pk}/"
        data = {"description": "No Auth"}
        response = self.unauthenticated_client.patch(url, data)
        self.assertEqual(response.status_code, 401)

    # --- CONVERSION PROGRESS ---
    def test_conversion_progress_success(self):
        url = f"/api/conversion-progress/{self.other_video.pk}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("progress", response.data)
        self.assertIn("current_resolution", response.data)

    def test_conversion_progress_reports_status(self):
        # Without the status a failed conversion looks like "0 % processed" forever (VFX-35).
        Video.objects.filter(pk=self.other_video.pk).update(status="failed")
        response = self.client.get(f"/api/conversion-progress/{self.other_video.pk}/")
        self.assertEqual(response.data["status"], "failed")

    # --- STATUS ---
    def test_list_and_detail_report_status(self):
        Video.objects.filter(pk=self.other_video.pk).update(status="ready")
        listed = self.client.get("/api/video/").data
        self.assertEqual(listed[0]["status"], "ready")
        detail = self.client.get(f"/api/video/{self.other_video.pk}/").data
        self.assertEqual(detail["status"], "ready")

    @patch("video_app.api.utils.subprocess.run", return_value=ffprobe_output())
    def test_upload_ignores_client_status(self, mock_run):
        data = {
            "title": "Status Video",
            "description": "Clients cannot set the status.",
            "category": "Comedy",
            "video_file": SimpleUploadedFile("test.mp4", b"fake content", content_type="video/mp4"),
            "status": "ready",
        }
        response = self.client.post("/api/upload/", data, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Video.objects.get(title="Status Video").status, "processing")

    def test_conversion_progress_invalid_video(self):
        url = "/api/conversion-progress/99999/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_conversion_progress_no_auth(self):
        url = f"/api/conversion-progress/{self.other_video.pk}/"
        response = self.unauthenticated_client.get(url)
        self.assertEqual(response.status_code, 401)

    # --- CLEAR CACHE ---
    def test_clear_cache_success(self):
        url = "/api/clear-cache/"
        response = self.admin_client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("status", response.data)

    def test_clear_cache_no_auth(self):
        url = "/api/clear-cache/"
        response = self.unauthenticated_client.post(url)
        self.assertEqual(response.status_code, 401)

    # --- VIDEO RESOLUTIONS (Created by task, here just DB test) ---
    def test_video_resolution_create_and_str(self):
        video = self.other_video
        res = VideoResolution.objects.create(
            original_video=video,
            resolution="720p",
            converted_file="videos/test_720p.mp4",
        )
        self.assertEqual(str(res), f"{video.title} - 720p")

    # --- VIDEO PROGRESS (Wiedergabefortschritt speichern & abrufen) ---
    def test_video_progress_create(self):
        from video_app.models import VideoProgress

        progress = VideoProgress.objects.create(
            user=self.user, video=self.other_video, progress_seconds=55
        )
        self.assertEqual(progress.progress_seconds, 55)
        self.assertEqual(str(progress.user), "video@test.de")
        self.assertEqual(str(progress.video), "Second Video")

    # --- EDGE CASES ---
    def test_upload_invalid_file_type(self):
        url = "/api/upload/"
        fake_file = SimpleUploadedFile(
            "fake.txt", b"just text", content_type="text/plain"
        )
        data = {
            "title": "Bad File",
            "description": "Should fail.",
            "category": "Comedy",
            "video_file": fake_file,
        }
        response = self.client.post(url, data, format="multipart")
        self.assertIn(response.status_code, [400, 415])

    def test_patch_video_no_data(self):
        url = f"/api/video/{self.other_video.pk}/"
        response = self.admin_client.patch(url, {})
        self.assertEqual(response.status_code, 200)

    def test_list_videos_empty(self):
        Video.objects.all().delete()
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        url = "/api/video/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_video_list_cache(self):
        url = "/api/video/"

        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        cache.set(STANDARD_VIDEOS_CACHE_KEY, [{"id": 1, "title": "From Cache"}], timeout=300)
        response = self.client.get(url)
        self.assertEqual(response.data[0]["title"], "From Cache")

    @patch("video_app.api.tasks.subprocess.run")
    def test_convert_video_calls_ffmpeg(self, mock_run):
        video = Video.objects.create(
            title="Job Video",
            description="Test",
            category="Action",
            video_file=self.video_file,
        )
        convert_video(video.id)
        self.assertTrue(mock_run.called)

    @patch("django_rq.get_queue")
    def test_signal_enqueue_tasks(self, mock_get_queue):
        mock_queue = mock_get_queue.return_value
        video = Video.objects.create(
            title="Signal Test",
            description="Signal Desc",
            category="Action",
            video_file=self.video_file,
        )
        self.assertTrue(mock_queue.enqueue.called)

    def test_get_base_name_and_extension(self):
        self.assertEqual(
            utils.get_base_name_and_extension("video.mp4"), ("video", "mp4")
        )
        self.assertEqual(
            utils.get_base_name_and_extension("movie.avi"), ("movie", "avi")
        )
        self.assertEqual(utils.get_base_name_and_extension("noext"), ("noext", None))

    def test_build_output_filename(self):
        self.assertEqual(
            utils.build_output_filename("foo", "720p", "mp4"), "foo_720p.mp4"
        )
        self.assertEqual(
            utils.build_output_filename("bar", "thumbnail", "jpg"), "bar_thumbnail.jpg"
        )

    def test_is_valid_video_extension(self):
        self.assertTrue(utils.is_valid_video_extension("mp4"))
        self.assertTrue(utils.is_valid_video_extension("mkv"))
        self.assertFalse(utils.is_valid_video_extension("exe"))
        self.assertFalse(utils.is_valid_video_extension(None))

    def test_get_ffmpeg_thumbnail_command(self):
        cmd = utils.get_ffmpeg_thumbnail_command("input", "output")
        self.assertIn("ffmpeg", cmd)
        self.assertIn("input", cmd)
        self.assertIn("output", cmd)

    def test_thumbnail_command_accepts_a_seek_position(self):
        default = utils.get_ffmpeg_thumbnail_command("input", "output")
        self.assertEqual(default[default.index("-ss") + 1], "00:00:05")
        cmd = utils.get_ffmpeg_thumbnail_command("input", "output", seek="00:00:00")
        self.assertEqual(cmd[cmd.index("-ss") + 1], "00:00:00")

    def test_thumbnail_command_overwrites_a_leftover_file(self):
        # A failed first attempt may leave an output file; without -y ffmpeg would refuse to overwrite it.
        cmd = utils.get_ffmpeg_thumbnail_command("input", "output")
        self.assertIn("-y", cmd)

    def test_thumbnail_command_fits_the_tile_size(self):
        # VFX-64: tiles show ~533x300, the hero is wider; 1280x720 covers both at 2x.
        cmd = utils.get_ffmpeg_thumbnail_command("input", "output")
        self.assertEqual(cmd[cmd.index("-s") + 1], "1280x720")
        self.assertEqual(cmd[cmd.index("-q:v") + 1], "5")

    def test_ffmpeg_commands_may_only_read_local_files(self):
        # Uploads are untrusted: a crafted file must not make ffmpeg or ffprobe open URLs.
        commands = [
            utils.get_ffmpeg_thumbnail_command("input", "output"),
            utils.get_ffmpeg_hls_command("input", "output", "base", 720)[0],
            utils.get_ffprobe_command("input"),
        ]
        for cmd in commands:
            position = cmd.index("-protocol_whitelist")
            self.assertEqual(cmd[position + 1], "file")
            self.assertLess(position, cmd.index("input"))

    def test_get_ffmpeg_hls_command(self):
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "height", 720)
        self.assertIn("-vf", cmd)
        self.assertIn("scale=-2:720", cmd)

    @patch("django.db.models.fields.files.FieldFile.save", return_value=None)
    @patch("video_app.api.tasks.default_storage.delete", return_value=True)
    @patch("video_app.api.tasks.default_storage.exists", return_value=True)
    @patch("video_app.api.tasks.ContentFile", return_value=b"img")
    @patch("video_app.api.tasks.open", new_callable=mock_open, read_data=b"img")
    @patch("video_app.api.tasks._thumbnail_written", return_value=True)
    @patch("video_app.api.tasks.subprocess.run", return_value=True)
    @patch(
        "video_app.api.tasks.get_ffmpeg_thumbnail_command",
        return_value=["ffmpeg", "args"],
    )
    @patch(
        "video_app.api.tasks.build_output_filename", return_value="foo_thumbnail.jpg"
    )
    @patch(
        "video_app.api.tasks.get_base_name_and_extension", return_value=("foo", "mp4")
    )
    @patch("video_app.api.tasks.default_storage.path", side_effect=lambda x: x)
    def test_create_thumbnail_success(
        self,
        mock_path,
        mock_get_base,
        mock_build_out,
        mock_get_cmd,
        mock_subprocess,
        mock_written,
        mock_openfile,
        mock_contentfile,
        mock_exists,
        mock_delete,
        mock_save,
    ):
        video = Video.objects.create(
            title="Video 1",
            description="desc",
            category="Action",
            video_file="videos/foo.mp4",
        )
        tasks.create_thumbnail(video.id)
        video.refresh_from_db()
        self.assertIn(video.status, ["processing", "ready", "failed"])

    @patch("video_app.api.tasks.set_video_failed")
    @patch("django.db.models.fields.files.FieldFile.save", return_value=None)
    @patch("video_app.api.tasks.open", new_callable=mock_open, read_data=b"img")
    @patch("video_app.api.tasks._thumbnail_written", return_value=True)
    @patch("video_app.api.tasks.subprocess.run")
    def test_short_clip_falls_back_to_the_first_frame(
        self, mock_run, mock_written, mock_openfile, mock_save, mock_failed
    ):
        mock_run.side_effect = [
            subprocess.CalledProcessError(234, ["ffmpeg"]),
            MagicMock(returncode=0),
        ]
        video = Video.objects.create(
            title="Short", description="desc", category="Action"
        )
        tasks._run_thumbnail_command("in.mp4", "out.jpg", video, "out.jpg")
        self.assertEqual(mock_run.call_count, 2)
        first, second = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(first[first.index("-ss") + 1], "00:00:05")
        self.assertEqual(second[second.index("-ss") + 1], "00:00:00")
        self.assertTrue(mock_save.called)
        mock_failed.assert_not_called()

    @patch("video_app.api.tasks.set_video_failed")
    @patch("django.db.models.fields.files.FieldFile.save", return_value=None)
    @patch("video_app.api.tasks.open", new_callable=mock_open, read_data=b"img")
    @patch("video_app.api.tasks._thumbnail_written", side_effect=[False, True])
    @patch("video_app.api.tasks.subprocess.run")
    def test_short_clip_falls_back_when_ffmpeg_exits_cleanly_without_a_file(
        self, mock_run, mock_written, mock_openfile, mock_save, mock_failed
    ):
        mock_run.return_value = MagicMock(returncode=0)
        video = Video.objects.create(
            title="Quiet", description="desc", category="Action"
        )
        tasks._run_thumbnail_command("in.mp4", "out.jpg", video, "out.jpg")
        self.assertEqual(mock_run.call_count, 2)
        second = mock_run.call_args_list[1].args[0]
        self.assertEqual(second[second.index("-ss") + 1], "00:00:00")
        self.assertTrue(mock_save.called)
        mock_failed.assert_not_called()

    @patch("video_app.api.tasks._thumbnail_written", return_value=False)
    @patch("video_app.api.tasks.subprocess.run")
    def test_thumbnail_fails_when_both_attempts_exit_cleanly_without_a_file(
        self, mock_run, mock_written
    ):
        mock_run.return_value = MagicMock(returncode=0)
        video = Video.objects.create(
            title="Empty", description="desc", category="Action"
        )
        tasks._run_thumbnail_command("in.mp4", "out.jpg", video, "out.jpg")
        self.assertEqual(mock_run.call_count, 2)
        video.refresh_from_db()
        self.assertEqual(video.status, "failed")

    def test_thumbnail_counts_as_written_only_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.jpg")
            empty = os.path.join(tmp, "empty.jpg")
            filled = os.path.join(tmp, "filled.jpg")
            open(empty, "wb").close()
            with open(filled, "wb") as f:
                f.write(b"img")
            self.assertFalse(tasks._thumbnail_written(missing))
            self.assertFalse(tasks._thumbnail_written(empty))
            self.assertTrue(tasks._thumbnail_written(filled))

    @patch("video_app.api.tasks.subprocess.run")
    def test_thumbnail_fails_when_both_attempts_fail(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(234, ["ffmpeg"])
        video = Video.objects.create(
            title="Broken", description="desc", category="Action"
        )
        tasks._run_thumbnail_command("in.mp4", "out.jpg", video, "out.jpg")
        self.assertEqual(mock_run.call_count, 2)
        video.refresh_from_db()
        self.assertEqual(video.status, "failed")

    @patch("video_app.api.tasks.default_storage.path", side_effect=lambda x: x)
    def test_create_thumbnail_invalid_file(self, mock_path):
        video = Video.objects.create(
            title="NoFile", description="desc", category="Action"
        )
        tasks.create_thumbnail(video.id)
        video.refresh_from_db()
        self.assertEqual(video.status, "failed")

    @patch(
        "video_app.api.tasks.get_base_name_and_extension", return_value=("foo", "bad")
    )
    def test_convert_video_invalid_extension(self, mock_get_base):
        video = Video.objects.create(
            title="BadExt",
            description="desc",
            category="Action",
            video_file="videos/foo.bad",
        )
        tasks.convert_video(video.id)
        video.refresh_from_db()
        self.assertEqual(video.status, "failed")

    @patch(
        "video_app.api.tasks.get_base_name_and_extension", return_value=("foo", "mp4")
    )
    @patch("video_app.api.tasks.is_valid_video_extension", return_value=True)
    @patch("video_app.api.tasks.default_storage.path", side_effect=lambda x: x)
    @patch(
        "video_app.api.tasks.get_ffmpeg_hls_command",
        return_value=["ffmpeg", "args"],
    )
    @patch("video_app.api.tasks.subprocess.run", side_effect=Exception("fail"))
    def test_convert_video_exception(
        self, mock_run, mock_get_cmd, mock_path, mock_valid, mock_get_base
    ):
        video = Video.objects.create(
            title="SubprocessFail",
            description="desc",
            category="Action",
            video_file="videos/foo.mp4",
        )
        tasks.convert_video(video.id)
        video.refresh_from_db()
        self.assertEqual(video.status, "failed")

    @patch("django_rq.get_queue")
    def test_video_post_save_signal_enqueues_tasks(self, mock_get_queue):
        video = Video.objects.create(
            title="SignalTest",
            description="desc",
            category="Action",
            video_file="videos/foo.mp4",
        )
        self.assertTrue(mock_get_queue.return_value.enqueue.called)

    @patch("video_app.api.signals.default_storage.exists", return_value=True)
    @patch("video_app.api.signals.default_storage.delete")
    def test_video_pre_delete_signal_deletes_files(self, mock_delete, mock_exists):
        video = Video.objects.create(
            title="DelTest",
            description="desc",
            category="Action",
            video_file="videos/foo.mp4",
        )
        video.delete()
        self.assertTrue(mock_delete.called)

    @patch("video_app.api.views.os.path.exists", return_value=True)
    @patch("video_app.api.views.open", new_callable=mock_open, read_data=b"#EXTM3U")
    @patch("video_app.api.views.os.path.abspath", side_effect=lambda x: x)
    def test_hls_serve_m3u8_success(self, mock_abspath, mock_openfile, mock_exists):
        res = VideoResolution.objects.create(
            original_video=self.other_video,
            resolution="720p",
            converted_file="media/hls/vid/index.m3u8",
        )
        url = f"/api/video/{self.other_video.id}/720p/index.m3u8"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.apple.mpegurl")

    @patch("video_app.api.views.os.path.exists", return_value=True)
    @patch("video_app.api.views.open", new_callable=mock_open, read_data=b"FAKE_TS")
    @patch("video_app.api.views.os.path.abspath", side_effect=lambda x: x)
    def test_hls_serve_ts_success(self, mock_abspath, mock_openfile, mock_exists):
        res = VideoResolution.objects.create(
            original_video=self.other_video,
            resolution="480p",
            converted_file="media/hls/vid/index.m3u8",
        )
        url = f"/api/video/{self.other_video.id}/480p/index001.ts"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "video/mp2t")

    def test_hls_serve_file_not_found(self):
        VideoResolution.objects.create(
            original_video=self.other_video,
            resolution="360p",
            converted_file="media/hls/vid/index.m3u8",
        )
        url = f"/api/video/{self.other_video.id}/360p/nonexistent.ts"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_hls_serve_invalid_video(self):
        url = "/api/video/9999/720p/index.m3u8"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_hls_serve_invalid_resolution(self):
        url = f"/api/video/{self.other_video.id}/999p/index.m3u8"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class VideoOwnershipTests(APITestCase):
    """
    Roles and ownership (VFX-26).

    Standard videos have no owner and are visible to every logged-in account.
    Uploads by members are private: only the owner and admins can see or change them.
    """

    def setUp(self):
        cache.clear()
        self.admin = CustomUser.objects.create_user(
            username="admin", email="admin@test.de", password="pw", is_active=True, is_staff=True
        )
        self.alice = CustomUser.objects.create_user(
            username="alice", email="alice@test.de", password="pw", is_active=True
        )
        self.bob = CustomUser.objects.create_user(
            username="bob", email="bob@test.de", password="pw", is_active=True
        )
        self.standard = Video.objects.create(
            title="Standard", description="Catalog video", category="Action"
        )
        self.alice_video = Video.objects.create(
            title="Alice upload", description="Private", category="Comedy", owner=self.alice
        )
        self.admin_client = self._client_for(self.admin)
        self.alice_client = self._client_for(self.alice)
        self.bob_client = self._client_for(self.bob)
        probe = patch("video_app.api.utils.subprocess.run", return_value=ffprobe_output())
        probe.start()
        self.addCleanup(probe.stop)

    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _upload(self, client, title="New upload", **extra):
        video_file = SimpleUploadedFile("clip.mp4", b"fake content", content_type="video/mp4")
        data = {"title": title, "description": "d", "category": "Comedy", "video_file": video_file}
        data.update(extra)
        return client.post("/api/upload/", data, format="multipart")

    def _titles(self, response):
        return {video["title"] for video in response.data}

    # --- UPLOAD ---
    def test_member_upload_is_owned_by_member(self):
        response = self._upload(self.bob_client, "Bob upload")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Video.objects.get(title="Bob upload").owner, self.bob)

    def test_admin_upload_becomes_standard_video(self):
        response = self._upload(self.admin_client, "New standard")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Video.objects.get(title="New standard").owner)

    def test_owner_cannot_be_set_by_client(self):
        self._upload(self.bob_client, "Spoofed", owner=self.alice.pk)
        self.assertEqual(Video.objects.get(title="Spoofed").owner, self.bob)

    # --- LIST ---
    def test_member_sees_standard_and_own_videos(self):
        response = self.alice_client.get("/api/video/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._titles(response), {"Standard", "Alice upload"})

    def test_member_does_not_see_foreign_uploads(self):
        response = self.bob_client.get("/api/video/")
        self.assertEqual(self._titles(response), {"Standard"})

    def test_cached_list_does_not_leak_foreign_uploads(self):
        self.alice_client.get("/api/video/")
        self.admin_client.get("/api/video/")
        response = self.bob_client.get("/api/video/")
        self.assertEqual(self._titles(response), {"Standard"})

    def test_admin_sees_all_videos(self):
        response = self.admin_client.get("/api/video/")
        self.assertEqual(self._titles(response), {"Standard", "Alice upload"})

    def test_list_marks_visibility_and_ownership(self):
        response = self.alice_client.get("/api/video/")
        by_title = {video["title"]: video for video in response.data}
        self.assertEqual(by_title["Standard"]["visibility"], "public")
        self.assertFalse(by_title["Standard"]["is_own"])
        self.assertEqual(by_title["Alice upload"]["visibility"], "private")
        self.assertTrue(by_title["Alice upload"]["is_own"])

    def test_list_sorts_standard_and_uploads_by_time(self):
        # A full second serialises without fraction ("...00Z") and would sort
        # after "...00.500000Z" as a string, although it is older.
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
        Video.objects.filter(pk=self.standard.pk).update(created_at=base)
        Video.objects.filter(pk=self.alice_video.pk).update(
            created_at=base + timedelta(milliseconds=500)
        )
        response = self.alice_client.get("/api/video/")
        self.assertEqual([video["title"] for video in response.data], ["Alice upload", "Standard"])

    # --- DETAIL, STREAM, CONVERSION PROGRESS ---
    def test_foreign_private_video_is_not_found(self):
        VideoResolution.objects.create(
            original_video=self.alice_video,
            resolution="720p",
            converted_file="media/hls/vid/index.m3u8",
        )
        urls = [
            f"/api/video/{self.alice_video.pk}/",
            f"/api/video/{self.alice_video.pk}/720p/index.m3u8",
            f"/api/conversion-progress/{self.alice_video.pk}/",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.bob_client.get(url).status_code, 404)

    def test_owner_and_admin_can_open_private_video(self):
        url = f"/api/video/{self.alice_video.pk}/"
        self.assertEqual(self.alice_client.get(url).status_code, 200)
        self.assertEqual(self.admin_client.get(url).status_code, 200)

    # --- PATCH ---
    def test_member_cannot_change_standard_video(self):
        response = self.bob_client.patch(f"/api/video/{self.standard.pk}/", {"title": "Hacked"})
        self.assertEqual(response.status_code, 403)
        self.standard.refresh_from_db()
        self.assertEqual(self.standard.title, "Standard")

    def test_member_cannot_change_foreign_upload(self):
        response = self.bob_client.patch(f"/api/video/{self.alice_video.pk}/", {"title": "Hacked"})
        self.assertEqual(response.status_code, 404)

    def test_owner_can_change_own_upload(self):
        response = self.alice_client.patch(f"/api/video/{self.alice_video.pk}/", {"title": "Renamed"})
        self.assertEqual(response.status_code, 200)
        self.alice_video.refresh_from_db()
        self.assertEqual(self.alice_video.title, "Renamed")

    def test_owner_cannot_be_changed_by_patch(self):
        self.alice_client.patch(f"/api/video/{self.alice_video.pk}/", {"owner": self.bob.pk})
        self.alice_video.refresh_from_db()
        self.assertEqual(self.alice_video.owner, self.alice)

    def test_patch_does_not_put_private_video_into_cache(self):
        self.alice_client.patch(f"/api/video/{self.alice_video.pk}/", {"title": "Renamed"})
        response = self.bob_client.get("/api/video/")
        self.assertEqual(self._titles(response), {"Standard"})

    # --- DELETE ---
    def test_owner_can_delete_own_upload(self):
        response = self.alice_client.delete(f"/api/video/{self.alice_video.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Video.objects.filter(pk=self.alice_video.pk).exists())

    def test_member_cannot_delete_foreign_upload(self):
        response = self.bob_client.delete(f"/api/video/{self.alice_video.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Video.objects.filter(pk=self.alice_video.pk).exists())

    def test_member_cannot_delete_standard_video(self):
        response = self.bob_client.delete(f"/api/video/{self.standard.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Video.objects.filter(pk=self.standard.pk).exists())

    def test_admin_can_delete_any_video(self):
        self.assertEqual(self.admin_client.delete(f"/api/video/{self.standard.pk}/").status_code, 204)
        self.assertEqual(self.admin_client.delete(f"/api/video/{self.alice_video.pk}/").status_code, 204)

    # --- ACCOUNT DELETION ---
    def test_hard_delete_removes_uploads(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.alice_video.video_file = SimpleUploadedFile(
                "alice.mp4", b"fake content", content_type="video/mp4"
            )
            self.alice_video.save()
            file_path = self.alice_video.video_file.path
            self.assertTrue(os.path.exists(file_path))

            response = self.admin_client.delete(f"/api/admin/delete-account/{self.alice.pk}/")

            self.assertEqual(response.status_code, 204)
            self.assertFalse(Video.objects.filter(pk=self.alice_video.pk).exists())
            self.assertFalse(os.path.exists(file_path))
            self.assertTrue(Video.objects.filter(pk=self.standard.pk).exists())

    def test_soft_delete_keeps_uploads_for_admin(self):
        response = self.alice_client.delete("/api/delete-account/", {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 204)
        response = self.admin_client.get(f"/api/video/{self.alice_video.pk}/")
        self.assertEqual(response.status_code, 200)

    # --- ADMIN ENDPOINTS ---
    def test_member_cannot_clear_cache(self):
        self.assertEqual(self.bob_client.post("/api/clear-cache/").status_code, 403)

    # django-rq uses the Django session, not the JWT cookie, so these tests log in with a session.
    def test_member_is_sent_to_admin_login_for_django_rq(self):
        self.client.force_login(self.bob)
        response = self.client.get("/api/django-rq/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/admin/login/"))

    @override_settings(STORAGES=PLAIN_STATIC_STORAGES)
    def test_admin_can_open_django_rq(self):
        self.client.force_login(self.admin)
        response = self.client.get("/api/django-rq/")
        self.assertEqual(response.status_code, 200)

    # --- DJANGO ADMIN ---
    @override_settings(STORAGES=PLAIN_STATIC_STORAGES)
    def test_admin_owner_filter_lists_only_accounts_with_uploads(self):
        superuser = CustomUser.objects.create_superuser(
            username="root", email="root@test.de", password="pw"
        )
        self.client.force_login(superuser)
        response = self.client.get("/admin/video_app/video/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"owner__id__exact={self.alice.pk}")
        self.assertNotContains(response, f"owner__id__exact={self.bob.pk}")


class UploadValidationTests(APITestCase):
    """
    Upload checks and quota (VFX-27, masterplan G6).

    Members may keep 2 videos at once; each video at most 50 MB and 2 minutes, MP4 or MOV.
    Admin uploads are standard videos and have no quota.
    """

    def setUp(self):
        cache.clear()
        self.member = CustomUser.objects.create_user(
            username="member", email="member@test.de", password="pw", is_active=True
        )
        self.admin = CustomUser.objects.create_user(
            username="admin", email="admin@test.de", password="pw", is_active=True, is_staff=True
        )
        self.member_client = APIClient()
        self.member_client.force_authenticate(user=self.member)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin)
        probe = patch("video_app.api.utils.subprocess.run", return_value=ffprobe_output())
        self.mock_run = probe.start()
        self.addCleanup(probe.stop)

    def _upload(self, client=None, name="clip.mp4", content=b"fake content", title="Clip"):
        video_file = SimpleUploadedFile(name, content, content_type="video/mp4")
        data = {"title": title, "description": "d", "category": "Comedy", "video_file": video_file}
        return (client or self.member_client).post("/api/upload/", data, format="multipart")

    def _own_video(self, title):
        return Video.objects.create(title=title, description="d", category="Comedy", owner=self.member)

    # --- FILE TYPE ---
    def test_mp4_is_accepted(self):
        self.assertEqual(self._upload(name="clip.mp4").status_code, 201)

    def test_mov_is_accepted(self):
        self.assertEqual(self._upload(name="clip.MOV").status_code, 201)

    def test_other_extension_is_rejected(self):
        for name in ["clip.avi", "clip.mkv"]:
            with self.subTest(name=name):
                response = self._upload(name=name)
                self.assertEqual(response.status_code, 400)
                self.assertIn("video_file", response.data)

    def test_container_that_is_not_mp4_or_mov_is_rejected(self):
        self.mock_run.return_value = ffprobe_output(format_name="matroska,webm")
        response = self._upload(name="renamed.mp4")
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)

    def test_file_ffprobe_cannot_read_is_rejected(self):
        self.mock_run.return_value = ffprobe_output(returncode=1)
        response = self._upload(name="broken.mp4")
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)

    def test_file_without_video_stream_is_rejected(self):
        self.mock_run.return_value = ffprobe_output(has_video=False)
        response = self._upload(name="audio-only.mp4")
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)

    # --- SIZE AND DURATION ---
    def test_file_over_50_mb_is_rejected_before_ffprobe(self):
        response = self._upload(content=b"0" * (50 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)
        self.mock_run.assert_not_called()

    def test_video_of_exactly_two_minutes_is_accepted(self):
        self.mock_run.return_value = ffprobe_output(duration="120.0")
        self.assertEqual(self._upload().status_code, 201)

    def test_video_over_two_minutes_is_rejected(self):
        self.mock_run.return_value = ffprobe_output(duration="120.5")
        response = self._upload()
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)

    def test_ffprobe_reads_the_uploaded_file_and_temp_copy_is_removed(self):
        self._upload()
        command = self.mock_run.call_args.args[0]
        self.assertEqual(command[0], "ffprobe")
        self.assertTrue(command[-1].endswith(".mp4"))
        self.assertFalse(os.path.exists(command[-1]))

    def test_missing_ffprobe_rejects_upload(self):
        self.mock_run.side_effect = FileNotFoundError("ffprobe")
        response = self._upload()
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)

    # --- QUOTA ---
    def test_member_may_have_two_videos_at_once(self):
        self._own_video("First")
        self.assertEqual(self._upload(title="Second").status_code, 201)

    def test_third_video_is_rejected_before_ffprobe(self):
        self._own_video("First")
        self._own_video("Second")
        response = self._upload(title="Third")
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)
        self.assertFalse(Video.objects.filter(title="Third").exists())
        self.mock_run.assert_not_called()

    def test_deleting_a_video_frees_a_slot(self):
        first = self._own_video("First")
        self._own_video("Second")
        self.assertEqual(self.member_client.delete(f"/api/video/{first.pk}/").status_code, 204)
        self.assertEqual(self._upload(title="Third").status_code, 201)

    def test_standard_videos_do_not_count_for_members(self):
        for title in ["Standard 1", "Standard 2", "Standard 3"]:
            Video.objects.create(title=title, description="d", category="Action")
        self.assertEqual(self._upload().status_code, 201)

    def test_admin_has_no_quota(self):
        for title in ["One", "Two", "Three"]:
            with self.subTest(title=title):
                self.assertEqual(self._upload(self.admin_client, title=title).status_code, 201)


class MediaProtectionTests(APITestCase):
    """
    Media delivery (VFX-30, decision variant a).

    No public /media/ route: HLS files and thumbnails only through the API with the
    same visibility rule as videos, never cached by shared proxies.
    """

    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media_root)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.alice = CustomUser.objects.create_user(
            username="alice", email="alice@test.de", password="pw", is_active=True
        )
        self.bob = CustomUser.objects.create_user(
            username="bob", email="bob@test.de", password="pw", is_active=True
        )
        self.alice_client = APIClient()
        self.alice_client.force_authenticate(user=self.alice)
        self.bob_client = APIClient()
        self.bob_client.force_authenticate(user=self.bob)

        self.video = Video.objects.create(
            title="Alice upload", description="d", category="Comedy", owner=self.alice,
            thumbnail_url=self._write("thumbnails/clip.jpg", b"JPEG"),
        )
        self.standard = Video.objects.create(
            title="Standard", description="d", category="Action",
            thumbnail_url=self._write("thumbnails/standard.jpg", b"STANDARD"),
        )
        self.hls_dir = os.path.join(self.media_root, "hls", "clip", "720p")
        self._write("hls/clip/720p/index.m3u8", b"#EXTM3U")
        self._write("hls/clip/720p/index000.ts", b"TS")
        self._write("hls/clip/720p/notes.txt", b"not a stream file")
        self._write("videos/other-original.mp4", b"ORIGINAL")
        VideoResolution.objects.create(
            original_video=self.video, resolution="720p", converted_file="hls/clip/720p/index.m3u8"
        )

    def _write(self, relative_path, content):
        path = os.path.join(self.media_root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return relative_path

    def _hls_url(self, filename):
        return f"/api/video/{self.video.pk}/720p/{filename}"

    # --- HLS ---
    def test_hls_files_are_served_privately(self):
        for filename in ["index.m3u8", "index000.ts"]:
            with self.subTest(filename=filename):
                response = self.alice_client.get(self._hls_url(filename))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "private")
                # Reading the stream lets the test client close the file safely.
                self.assertTrue(b"".join(response.streaming_content))

    def test_hls_serves_only_playlists_and_segments(self):
        for filename in ["notes.txt", ".."]:
            with self.subTest(filename=filename):
                self.assertEqual(self.alice_client.get(self._hls_url(filename)).status_code, 404)

    def test_hls_path_must_stay_inside_the_resolution_folder(self):
        for filename in ["../../../videos/other-original.mp4", "../720p_x/index.m3u8"]:
            with self.subTest(filename=filename):
                with self.assertRaises(Http404):
                    _get_hls_file_path(self.hls_dir, filename)

    # --- THUMBNAILS ---
    def test_list_points_thumbnails_to_the_api(self):
        response = self.alice_client.get("/api/video/")
        by_title = {video["title"]: video for video in response.data}
        self.assertTrue(
            by_title["Alice upload"]["thumbnail_url"].endswith(f"/api/video/{self.video.pk}/thumbnail/")
        )
        self.assertNotIn("/media/", by_title["Standard"]["thumbnail_url"])

    def test_owner_gets_own_thumbnail_privately(self):
        response = self.alice_client.get(f"/api/video/{self.video.pk}/thumbnail/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, max-age=86400")
        self.assertEqual(b"".join(response.streaming_content), b"JPEG")

    def test_thumbnail_sends_last_modified(self):
        response = self.alice_client.get(f"/api/video/{self.video.pk}/thumbnail/")
        b"".join(response.streaming_content)
        self.assertIn("Last-Modified", response)

    def test_unchanged_thumbnail_is_not_sent_again(self):
        first = self.alice_client.get(f"/api/video/{self.video.pk}/thumbnail/")
        b"".join(first.streaming_content)
        again = self.alice_client.get(
            f"/api/video/{self.video.pk}/thumbnail/", HTTP_IF_MODIFIED_SINCE=first["Last-Modified"]
        )
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again["Cache-Control"], "private, max-age=86400")

    def test_not_modified_still_checks_visibility(self):
        response = self.bob_client.get(
            f"/api/video/{self.video.pk}/thumbnail/", HTTP_IF_MODIFIED_SINCE="Sat, 01 Jan 2099 00:00:00 GMT"
        )
        self.assertEqual(response.status_code, 404)

    def test_standard_thumbnail_is_visible_to_every_member(self):
        response = self.bob_client.get(f"/api/video/{self.standard.pk}/thumbnail/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"STANDARD")

    def test_foreign_private_thumbnail_is_not_found(self):
        self.assertEqual(self.bob_client.get(f"/api/video/{self.video.pk}/thumbnail/").status_code, 404)

    def test_missing_thumbnail_is_not_found(self):
        video = Video.objects.create(title="No thumb", description="d", category="Comedy", owner=self.alice)
        self.assertEqual(self.alice_client.get(f"/api/video/{video.pk}/thumbnail/").status_code, 404)

    def test_thumbnail_requires_login(self):
        self.assertEqual(APIClient().get(f"/api/video/{self.standard.pk}/thumbnail/").status_code, 401)

    # --- NO PUBLIC /media/ ---
    def test_media_route_is_gone_even_with_old_setting(self):
        import core.urls

        with patch.dict(os.environ, {"SERVE_MEDIA_THROUGH_DJANGO": "1"}):
            urls = importlib.reload(core.urls)
        self.addCleanup(importlib.reload, core.urls)
        with self.assertRaises(Resolver404):
            resolve("/media/videos/other-original.mp4", urlconf=urls)

    def test_api_does_not_expose_media_paths(self):
        # VFX-48: no dead /media/ links, no internal paths or original file names.
        Video.objects.filter(pk=self.video.pk).update(video_file="videos/secret-name.mp4")
        responses = {
            "detail": self.alice_client.get(f"/api/video/{self.video.pk}/").data,
            "list": next(v for v in self.alice_client.get("/api/video/").data if v["id"] == self.video.pk),
        }
        for name, data in responses.items():
            with self.subTest(response=name):
                self.assertNotIn("video_file", data)
                self.assertNotIn("converted_file", data["resolutions"][0])
                self.assertEqual(data["resolutions"][0]["resolution"], "720p")
                self.assertNotIn("/media/", str(data))
                self.assertNotIn("secret-name", str(data))

    @patch("video_app.api.utils.subprocess.run", return_value=ffprobe_output())
    def test_video_file_cannot_be_replaced_by_patch(self, mock_run):
        # VFX-48: a replaced original would never be converted or deleted (quota bypass).
        Video.objects.filter(pk=self.video.pk).update(video_file="videos/clip.mp4")
        new_file = SimpleUploadedFile("other.mp4", b"fake content", content_type="video/mp4")
        response = self.alice_client.patch(
            f"/api/video/{self.video.pk}/", {"video_file": new_file}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("video_file", response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.video_file.name, "videos/clip.mp4")

    # --- ORIGINALS ---
    @patch("video_app.api.tasks.subprocess.run")
    def test_original_is_deleted_after_successful_conversion(self, mock_run):
        video = Video.objects.create(
            title="Convert", description="d", category="Comedy", owner=self.alice,
            video_file=self._write("videos/convert.mp4", b"ORIGINAL"),
        )
        original_path = os.path.join(self.media_root, "videos", "convert.mp4")

        convert_video(video.id)

        video.refresh_from_db()
        self.assertEqual(video.resolutions.count(), 3)
        self.assertFalse(os.path.exists(original_path))
        self.assertFalse(video.video_file)

    @patch("video_app.api.tasks.subprocess.run", side_effect=Exception("ffmpeg failed"))
    def test_original_is_kept_when_conversion_fails(self, mock_run):
        video = Video.objects.create(
            title="Broken", description="d", category="Comedy", owner=self.alice,
            video_file=self._write("videos/broken.mp4", b"ORIGINAL"),
        )

        convert_video(video.id)

        video.refresh_from_db()
        self.assertEqual(video.status, "failed")
        self.assertTrue(os.path.exists(os.path.join(self.media_root, "videos", "broken.mp4")))
        self.assertEqual(video.video_file.name, "videos/broken.mp4")

    # --- CONVERSION LIMIT ---
    @patch("video_app.api.tasks.subprocess.run")
    def test_owned_video_is_converted_with_the_length_limit(self, mock_run):
        # The upload check trusts the container header; the conversion must cap the length itself.
        video = Video.objects.create(
            title="Limited", description="d", category="Comedy", owner=self.alice,
            video_file=self._write("videos/limited.mp4", b"ORIGINAL"),
        )
        convert_video(video.id)
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertEqual(len(commands), 3)
        for cmd in commands:
            self.assertEqual(cmd[cmd.index("-t") + 1], str(utils.MAX_VIDEO_DURATION_SECONDS))

    @patch("video_app.api.tasks.subprocess.run")
    def test_standard_video_is_converted_without_a_length_limit(self, mock_run):
        video = Video.objects.create(
            title="Catalog", description="d", category="Comedy",
            video_file=self._write("videos/catalog.mp4", b"ORIGINAL"),
        )
        convert_video(video.id)
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertEqual(len(commands), 3)
        for cmd in commands:
            self.assertNotIn("-t", cmd)

    # --- HLS FOLDER CLEANUP ---
    def test_deleting_a_video_removes_its_folder_even_without_resolution_rows(self):
        # A stopped conversion leaves segments but no row; the folder belongs to this video alone.
        video = Video.objects.create(title="Stopped", description="d", category="Comedy", owner=self.alice)
        other = Video.objects.create(title="Other", description="d", category="Comedy", owner=self.alice)
        self._write(f"hls/{video.pk}/480p/index000.ts", b"TS")
        sibling = self._write(f"hls/{video.pk}0/480p/index000.ts", b"TS")
        others = self._write(f"hls/{other.pk}/480p/index000.ts", b"TS")
        old_layout = self._write("hls/clip/720p/index000.ts", b"TS")
        pk = video.pk
        self.assertTrue(os.path.exists(os.path.join(self.media_root, "hls", str(pk))))
        video.delete()
        self.assertFalse(os.path.exists(os.path.join(self.media_root, "hls", str(pk))))
        for kept in (sibling, others, old_layout):
            self.assertTrue(os.path.exists(os.path.join(self.media_root, kept)), kept)


class VideoListCachePrivacyTests(APITestCase):
    """
    The shared cache must not carry the progress of whoever filled it (VFX-44).
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.first = CustomUser.objects.create_user(
            username="first", email="first@test.de", password="First123!", is_active=True
        )
        self.second = CustomUser.objects.create_user(
            username="second", email="second@test.de", password="Second123!", is_active=True
        )
        with patch("video_app.api.signals.django_rq.get_queue"):
            self.video = Video.objects.create(title="Standard", description="d", category="c")
        VideoProgress.objects.create(user=self.first, video=self.video, progress_seconds=42)

    def progress_of(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/video/")
        self.assertEqual(response.status_code, 200)
        return response.data[0]["user_progress"]

    def test_each_person_gets_their_own_progress_from_the_cache(self):
        self.assertEqual(self.progress_of(self.first), 42)
        self.assertEqual(self.progress_of(self.second), 0)
        self.assertEqual(self.progress_of(self.first), 42)

    def test_cache_entry_holds_no_progress(self):
        self.progress_of(self.first)
        cached = cache.get(STANDARD_VIDEOS_CACHE_KEY)
        self.assertEqual(len(cached), 1)
        self.assertNotIn("user_progress", cached[0])

    def test_progress_comes_from_one_query_for_a_cached_list(self):
        self.progress_of(self.first)
        with patch("video_app.api.signals.django_rq.get_queue"):
            for number in range(3):
                video = Video.objects.create(title=f"Standard {number}", description="d", category="c")
                VideoProgress.objects.create(user=self.first, video=video, progress_seconds=number)
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        self.progress_of(self.first)
        self.client.force_authenticate(user=self.first)
        # Own uploads check and progress, nothing per video.
        with self.assertNumQueries(2):
            self.client.get("/api/video/")


class UploadThrottlingTests(APITestCase):
    """
    Upload rate limit per account (VFX-29, /secure 2026-11-02 S2).

    Rejected uploads count too: each check can run ffprobe for up to 30 s.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.member = CustomUser.objects.create_user(
            username="member", email="member@test.de", password="pw", is_active=True
        )
        self.member_client = APIClient()
        self.member_client.force_authenticate(user=self.member)

    def _upload(self, client):
        video_file = SimpleUploadedFile("clip.avi", b"fake content", content_type="video/x-msvideo")
        data = {"title": "Clip", "description": "d", "category": "Comedy", "video_file": video_file}
        return client.post("/api/upload/", data, format="multipart")

    def test_uploads_are_limited_per_account_including_rejected_ones(self):
        for _ in range(10):
            self.assertEqual(self._upload(self.member_client).status_code, 400)
        self.assertEqual(self._upload(self.member_client).status_code, 429)

    def test_upload_limit_is_per_account(self):
        for _ in range(10):
            self._upload(self.member_client)
        other = CustomUser.objects.create_user(
            username="other", email="other@test.de", password="pw", is_active=True
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        self.assertEqual(self._upload(other_client).status_code, 400)


class DemoCleanupTests(APITestCase):
    """
    Expired demo accounts disappear with their uploads and files (VFX-28, G5).
    """

    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media_root)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

    def test_cleanup_deletes_demo_uploads_and_files(self):
        demo = CustomUser.objects.create_user(
            username="demo-x", email="demo-x@demo.invalid", is_active=True, is_demo=True
        )
        CustomUser.objects.filter(pk=demo.pk).update(
            date_joined=datetime.now(dt_timezone.utc) - timedelta(hours=3)
        )
        path = os.path.join(self.media_root, "videos", "demo.mp4")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"ORIGINAL")
        with patch("video_app.api.signals.django_rq.get_queue"):
            video = Video.objects.create(
                title="Demo upload", description="d", category="Comedy",
                owner=demo, video_file="videos/demo.mp4",
            )

        call_command("delete_expired_demos", stdout=StringIO())

        self.assertFalse(Video.objects.filter(pk=video.pk).exists())
        self.assertFalse(os.path.exists(path))


class HlsFolderTests(APITestCase):
    """
    Every video converts into its own folder, so equal file names never share streams (X1 findings 1 and 4).
    """

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        queue = patch("video_app.api.signals.django_rq.get_queue")
        self.queue = queue.start()
        self.addCleanup(queue.stop)

    def _write(self, relative_path, content):
        path = os.path.join(self.media, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return relative_path

    def _fake_ffmpeg(self, command, **kwargs):
        """
        Stands in for ffmpeg only: writes a playlist and a segment holding the bytes of the input.
        """
        with open(command[command.index("-i") + 1], "rb") as f:
            content = f.read()
        out_dir = os.path.dirname(command[-1])
        with open(os.path.join(out_dir, "index.m3u8"), "wb") as f:
            f.write(b"#EXTM3U")
        with open(os.path.join(out_dir, "index000.ts"), "wb") as f:
            f.write(content)

    def _convert(self, title, content):
        video = Video.objects.create(
            title=title, description="d", category="Comedy",
            video_file=self._write("videos/clip.mp4", content),
        )
        with patch("video_app.api.tasks.subprocess.run", side_effect=self._fake_ffmpeg):
            convert_video(video.id)
        return video

    def _segment(self, video, resolution="720p"):
        res = video.resolutions.get(resolution=resolution)
        with open(os.path.join(os.path.dirname(res.converted_file.path), "index000.ts"), "rb") as f:
            return f.read()

    def test_equal_file_names_get_separate_folders_and_keep_their_own_streams(self):
        first = self._convert("First", b"FIRST")
        # The original is gone after conversion, so the next upload can have the same name.
        self.assertFalse(Video.objects.get(pk=first.pk).video_file)
        second = self._convert("Second", b"SECOND")
        folders = {
            os.path.dirname(r.converted_file.name)
            for video in (first, second)
            for r in video.resolutions.all()
        }
        self.assertEqual(len(folders), 6)
        self.assertEqual(self._segment(first), b"FIRST")
        self.assertEqual(self._segment(second), b"SECOND")
        self.assertEqual(sorted(os.listdir(os.path.join(self.media, "hls"))), sorted([str(first.pk), str(second.pk)]))

    def test_deleting_one_video_keeps_the_stream_of_the_other(self):
        first = self._convert("First", b"FIRST")
        second = self._convert("Second", b"SECOND")
        first.delete()
        self.assertEqual(self._segment(second), b"SECOND")
        self.assertFalse(os.path.exists(os.path.join(self.media, "hls", str(first.pk))))

    def _old_layout_video(self, title, folder="hls/videos/shared/480p"):
        self._write(f"{folder}/index.m3u8", b"#EXTM3U")
        video = Video.objects.create(title=title, description="d", category="Comedy")
        VideoResolution.objects.create(
            original_video=video, resolution="480p", converted_file=f"{folder}/index.m3u8"
        )
        return video

    def test_shared_old_layout_folder_is_deleted_only_with_its_last_video(self):
        folder = os.path.join(self.media, "hls", "videos", "shared", "480p")
        first = self._old_layout_video("First")
        second = self._old_layout_video("Second")
        first.delete()
        self.assertTrue(os.path.exists(os.path.join(folder, "index.m3u8")))
        second.delete()
        self.assertFalse(os.path.exists(folder))

    def test_old_layout_folder_of_a_single_video_is_still_deleted(self):
        video = self._old_layout_video("Only", folder="hls/videos/alone/480p")
        video.delete()
        self.assertFalse(os.path.exists(os.path.join(self.media, "hls", "videos", "alone", "480p")))


class StaleVideoWriteTests(APITestCase):
    """
    A job holding an old instance must not bring a deleted video back (X1 finding 3).
    """

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        queue = patch("video_app.api.signals.django_rq.get_queue")
        self.queue = queue.start()
        self.addCleanup(queue.stop)
        self.video = Video.objects.create(title="Stale", description="d", category="Comedy")
        self.queue.reset_mock()

    def _assert_stays_deleted(self):
        self.assertFalse(Video.objects.filter(title="Stale").exists())
        self.queue.return_value.enqueue.assert_not_called()

    def test_progress_write_after_delete_does_not_recreate_the_video(self):
        stale = Video.objects.get(pk=self.video.pk)
        self.video.delete()
        tasks.update_video_progress(stale, 1, 3, "480p")
        self._assert_stays_deleted()

    def test_failed_write_after_delete_does_not_recreate_the_video(self):
        stale = Video.objects.get(pk=self.video.pk)
        self.video.delete()
        tasks.set_video_failed(stale)
        self._assert_stays_deleted()

    def test_thumbnail_write_after_delete_does_not_recreate_the_video_or_keep_the_file(self):
        stale = Video.objects.get(pk=self.video.pk)
        self.video.delete()
        output = os.path.join(self.media, "stale_thumbnail.jpg")
        with open(output, "wb") as f:
            f.write(b"img")
        with patch("video_app.api.tasks.subprocess.run"):
            tasks._run_thumbnail_command("in.mp4", output, stale, "stale_thumbnail.jpg")
        self._assert_stays_deleted()
        self.assertEqual(os.listdir(os.path.join(self.media, "thumbnails")) if os.path.isdir(os.path.join(self.media, "thumbnails")) else [], [])

    def test_writes_for_a_live_video_still_land_in_the_row(self):
        stale = Video.objects.get(pk=self.video.pk)
        tasks.update_video_progress(stale, 1, 3, "480p")
        row = Video.objects.get(pk=self.video.pk)
        self.assertEqual((row.status, row.conversion_progress, row.current_resolution), ("ready", 33, "480p"))
        tasks.set_video_failed(stale)
        row.refresh_from_db()
        self.assertEqual((row.status, row.conversion_progress), ("failed", 0))
        self.queue.return_value.enqueue.assert_not_called()

    def test_thumbnail_of_a_live_video_is_saved_to_the_row(self):
        output = os.path.join(self.media, "stale_thumbnail.jpg")
        with open(output, "wb") as f:
            f.write(b"img")
        with patch("video_app.api.tasks.subprocess.run"):
            tasks._run_thumbnail_command("in.mp4", output, self.video, "stale_thumbnail.jpg")
        row = Video.objects.get(pk=self.video.pk)
        self.assertTrue(row.thumbnail_url.name.startswith("thumbnails/"))
        self.assertTrue(os.path.exists(os.path.join(self.media, row.thumbnail_url.name)))


CATALOG_LOADER = "video_app.management.commands.import_catalog.load_catalog"


class ImportCatalogCommandTests(APITestCase):
    """
    VFX-62: fill the standard catalog from a folder of sample clips on the server.
    """

    def setUp(self):
        self.source = tempfile.mkdtemp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.source, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        os.makedirs(os.path.join(self.source, "more clips"))
        for name in ("golf_sports.mp4", "segelschiff_mstery.mp4", "more clips/bird_2.mp4", "notes.txt"):
            with open(os.path.join(self.source, name), "wb") as f:
                f.write(b"fake video")
        queue = patch("video_app.api.signals.django_rq.get_queue")
        self.queue = queue.start()
        self.addCleanup(queue.stop)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        cache.delete(STANDARD_VIDEOS_CACHE_KEY)
        self.addCleanup(cache.delete, STANDARD_VIDEOS_CACHE_KEY)
        self.catalog = {
            "golf_sports": {"title": "Golf", "description": "Golf on a green.", "category": "Sports"},
            "segelschiff_mstery": {"title": "Segelschiff", "description": "A ship.", "category": "Mystery"},
            "bird_2": {"title": "Bird 2", "description": "A bird.", "category": "Documentary"},
        }
        catalog = patch(CATALOG_LOADER, side_effect=lambda: self.catalog)
        catalog.start()
        self.addCleanup(catalog.stop)

    def _import(self, *args, stderr=None):
        out = StringIO()
        call_command("import_catalog", self.source, *args, stdout=out, stderr=stderr or StringIO())
        return out.getvalue()

    def _stored_standard_video(self, title="Old clip"):
        """
        Standard or private video with a real original, thumbnail and HLS folder in MEDIA_ROOT.
        """
        video = Video.objects.create(title=title, description="d", category="Comedy")
        video.video_file.save(f"{title}.mp4", SimpleUploadedFile("x.mp4", b"original"), save=False)
        video.thumbnail_url.save(f"{title}.jpg", SimpleUploadedFile("x.jpg", b"thumb"), save=False)
        video.save()
        hls_dir = os.path.join(self.media, "hls", title, "720p")
        os.makedirs(hls_dir)
        with open(os.path.join(hls_dir, "index.m3u8"), "wb") as f:
            f.write(b"#EXTM3U")
        VideoResolution.objects.create(
            original_video=video, resolution="720p", converted_file=f"hls/{title}/720p/index.m3u8"
        )
        return video

    def _stored_files(self, video):
        return [
            os.path.join(self.media, video.video_file.name),
            os.path.join(self.media, video.thumbnail_url.name),
            os.path.join(self.media, "hls", video.title, "720p", "index.m3u8"),
        ]

    def test_imports_every_mp4_as_standard_video_with_category_from_the_name(self):
        self._import()
        videos = {v.title: v for v in Video.objects.all()}
        self.assertEqual(set(videos), {"Golf", "Segelschiff", "Bird 2"})
        self.assertEqual(videos["Golf"].category, "Sports")
        self.assertEqual(videos["Segelschiff"].category, "Mystery")
        self.assertEqual(videos["Bird 2"].category, "Documentary")
        self.assertTrue(all(v.owner_id is None for v in videos.values()))
        self.assertTrue(all(v.status == "processing" for v in videos.values()))

    def test_copies_the_file_and_starts_the_conversion(self):
        self._import()
        video = Video.objects.get(title="Golf")
        self.assertTrue(os.path.exists(os.path.join(self.media, video.video_file.name)))
        enqueued = [c.args[0].__name__ for c in self.queue.return_value.enqueue.call_args_list]
        self.assertEqual(enqueued.count("convert_video"), 3)

    def test_running_twice_does_not_duplicate_videos(self):
        self._import()
        output = self._import()
        self.assertEqual(Video.objects.count(), 3)
        self.assertIn("0 imported, 3 skipped", output)

    def test_uses_title_description_and_category_from_catalog(self):
        self.catalog["segelschiff_mstery"] = {"title": "Sailing Ship", "description": "A ship at sea.", "category": "Crime"}
        self._import()
        video = Video.objects.get(title="Sailing Ship")
        self.assertEqual(video.description, "A ship at sea.")
        self.assertEqual(video.category, "Crime")

    def test_unknown_file_falls_back_to_file_name_and_warns(self):
        stderr = StringIO()
        self.catalog.clear()
        self._import(stderr=stderr)
        video = Video.objects.get(title="Golf")
        self.assertEqual(video.category, "Sports")
        self.assertEqual(video.description, "Sample clip from the Videoflix demo catalog.")
        self.assertIn("golf_sports", stderr.getvalue())

    def test_catalog_descriptions_are_unique_and_categories_valid(self):
        from video_app.management.commands.import_catalog import CATALOG_PATH

        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
        categories = {value for value, _ in Video.CATEGORY_CHOICES}
        for name, entry in catalog.items():
            self.assertEqual(set(entry), {"title", "description", "category"}, name)
            self.assertTrue(entry["title"].strip() and entry["description"].strip(), name)
            self.assertLessEqual(len(entry["title"]), 150, name)
            self.assertLessEqual(len(entry["description"]), 500, name)
            self.assertIn(entry["category"], categories, name)
        descriptions = [entry["description"] for entry in catalog.values()]
        self.assertEqual(len(descriptions), len(set(descriptions)))
        titles = [entry["title"] for entry in catalog.values()]
        self.assertEqual(len(titles), len(set(titles)))

    def test_replace_deletes_standard_videos_and_their_files(self):
        old = self._stored_standard_video()
        files = self._stored_files(old)
        self.assertTrue(all(os.path.exists(path) for path in files))
        self._import("--replace")
        self.assertFalse(Video.objects.filter(pk=old.pk).exists())
        self.assertFalse(any(os.path.exists(path) for path in files))
        self.assertEqual(set(Video.objects.values_list("title", flat=True)), {"Golf", "Segelschiff", "Bird 2"})

    def test_replace_keeps_uploads_of_users(self):
        user = CustomUser.objects.create_user(email="owner@example.com", password="Passw0rd!x")
        upload = self._stored_standard_video("Mine")
        Video.objects.filter(pk=upload.pk).update(owner=user)
        files = self._stored_files(upload)
        self._import("--replace")
        self.assertTrue(Video.objects.filter(pk=upload.pk, owner=user).exists())
        self.assertTrue(all(os.path.exists(path) for path in files))

    def test_replace_clears_the_standard_video_cache(self):
        cache.set(STANDARD_VIDEOS_CACHE_KEY, ["stale"])
        self._import("--replace")
        self.assertIsNone(cache.get(STANDARD_VIDEOS_CACHE_KEY))

    def test_replace_with_empty_folder_fails_and_deletes_nothing(self):
        old = self._stored_standard_video()
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with open(os.path.join(empty, "notes.txt"), "wb") as f:
            f.write(b"no clips")
        for folder in (empty, os.path.join(empty, "missing")):
            with self.assertRaises(CommandError):
                call_command("import_catalog", folder, "--replace", stdout=StringIO())
        self.assertTrue(Video.objects.filter(pk=old.pk).exists())
        self.assertTrue(all(os.path.exists(path) for path in self._stored_files(old)))

    def test_folder_inside_media_root_is_refused_and_deletes_nothing(self):
        old = self._stored_standard_video()
        private = os.path.join(self.media, "videos", "private.mp4")
        with open(private, "wb") as f:
            f.write(b"private upload")
        for folder in (self.media, os.path.join(self.media, "videos")):
            for args in ((), ("--replace",)):
                with self.assertRaises(CommandError):
                    call_command("import_catalog", folder, *args, stdout=StringIO())
        self.assertEqual(Video.objects.count(), 1)
        self.assertTrue(all(os.path.exists(path) for path in self._stored_files(old)))

    def _assert_replace_aborts(self):
        old = self._stored_standard_video()
        with self.assertRaises(CommandError):
            self._import("--replace")
        self.assertEqual(list(Video.objects.values_list("pk", flat=True)), [old.pk])
        self.assertTrue(all(os.path.exists(path) for path in self._stored_files(old)))

    def test_replace_aborts_before_deleting_when_a_clip_has_no_catalog_entry(self):
        del self.catalog["bird_2"]
        self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_a_catalog_entry_has_no_clip(self):
        self.catalog["missing_clip"] = {"title": "Missing", "description": "No file.", "category": "Crime"}
        self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_entry_fields_are_missing(self):
        del self.catalog["bird_2"]["description"]
        self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_the_category_is_invalid(self):
        self.catalog["bird_2"]["category"] = "Western"
        self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_a_title_is_duplicated(self):
        self.catalog["bird_2"]["title"] = "Golf"
        self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_a_clip_is_unreadable(self):
        real_open = Path.open

        def open_unless_golf(path, *args, **kwargs):
            if path.name == "golf_sports.mp4":
                raise PermissionError("denied")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", open_unless_golf):
            self._assert_replace_aborts()

    def test_replace_aborts_before_deleting_when_the_catalog_file_is_broken(self):
        old = self._stored_standard_video()
        with patch(CATALOG_LOADER, side_effect=ValueError("bad json")):
            with self.assertRaises(CommandError):
                self._import("--replace")
        self.assertTrue(Video.objects.filter(pk=old.pk).exists())

    def test_without_replace_an_unknown_clip_still_imports_with_a_warning(self):
        del self.catalog["bird_2"]
        stderr = StringIO()
        self._import(stderr=stderr)
        self.assertEqual(Video.objects.count(), 3)
        self.assertIn("bird_2", stderr.getvalue())

    def test_catalog_lookup_ignores_unicode_normalization_and_finds_subfolders(self):
        folder = os.path.join(self.source, "videos für projekt")
        os.makedirs(folder)
        nfd = unicodedata.normalize("NFD", "händchen_romance.mp4")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        with open(os.path.join(folder, nfd), "wb") as f:
            f.write(b"fake video")
        self.catalog["händchen_romance"] = {"title": "Hands", "description": "Two hands.", "category": "Romance"}
        stderr = StringIO()
        self._import("--replace", stderr=stderr)
        self.assertEqual(Video.objects.get(title="Hands").category, "Romance")
        self.assertEqual(stderr.getvalue(), "")

    def test_import_leaves_source_files_in_place(self):
        self._import("--replace")
        for name in ("golf_sports.mp4", "segelschiff_mstery.mp4", "more clips/bird_2.mp4"):
            self.assertTrue(os.path.exists(os.path.join(self.source, name)), name)


class ShrinkThumbnailsCommandTests(APITestCase):
    """
    VFX-64: scale existing Full HD thumbnails down; the original videos are gone after conversion.
    """

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        os.makedirs(os.path.join(self.media, "thumbnails"))

    def _video_with_thumbnail(self, title, size):
        from PIL import Image

        name = f"thumbnails/{title}.jpg"
        Image.new("RGB", size, (200, 30, 30)).save(os.path.join(self.media, name), quality=95)
        return Video.objects.create(title=title, description="d", category="Comedy", thumbnail_url=name)

    def _shrink(self):
        out = StringIO()
        call_command("shrink_thumbnails", stdout=out)
        return out.getvalue()

    def _size_of(self, video):
        from PIL import Image

        with Image.open(os.path.join(self.media, video.thumbnail_url.name)) as image:
            return image.size

    def test_scales_full_hd_thumbnail_to_1280x720(self):
        video = self._video_with_thumbnail("big", (1920, 1080))
        output = self._shrink()
        self.assertEqual(self._size_of(video), (1280, 720))
        self.assertIn("1 shrunk", output)

    def test_leaves_small_thumbnail_untouched(self):
        video = self._video_with_thumbnail("small", (1280, 720))
        path = os.path.join(self.media, video.thumbnail_url.name)
        before = os.path.getmtime(path)
        output = self._shrink()
        self.assertEqual(os.path.getmtime(path), before)
        self.assertIn("0 shrunk, 1 unchanged", output)

    def test_reports_missing_file_and_continues(self):
        Video.objects.create(title="gone", description="d", category="Comedy", thumbnail_url="thumbnails/gone.jpg")
        Video.objects.create(title="none", description="d", category="Comedy")
        video = self._video_with_thumbnail("big", (1920, 1080))
        output = self._shrink()
        self.assertEqual(self._size_of(video), (1280, 720))
        self.assertIn("1 missing", output)


class VideoStreamsTests(APITestCase):
    """
    Stream statistics endpoint: values come from the real playlist and segment files,
    visibility is the same as for the stream itself.
    """

    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media_root)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.alice = CustomUser.objects.create_user(
            username="alice", email="alice@test.de", password="pw", is_active=True
        )
        self.bob = CustomUser.objects.create_user(
            username="bob", email="bob@test.de", password="pw", is_active=True
        )
        self.alice_client = APIClient()
        self.alice_client.force_authenticate(user=self.alice)
        self.bob_client = APIClient()
        self.bob_client.force_authenticate(user=self.bob)
        self.video = Video.objects.create(
            title="Alice upload", description="d", category="Comedy", owner=self.alice
        )

    def _write(self, relative_path, content):
        path = os.path.join(self.media_root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def _stream(self, resolution, segments, target=4, video=None, lines=None):
        """Create a resolution with a playlist; segments is a list of (name, seconds, bytes or None)."""
        folder = f"hls/clip/{resolution}"
        body = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}"]
        for name, seconds, size in segments:
            body += [f"#EXTINF:{seconds:.6f},", name]
            if size is not None:
                self._write(f"{folder}/{name}", b"x" * size)
        body += lines or []
        body.append("#EXT-X-ENDLIST")
        self._write(f"{folder}/index.m3u8", "\n".join(body).encode())
        VideoResolution.objects.create(
            original_video=video or self.video,
            resolution=resolution,
            converted_file=f"{folder}/index.m3u8",
        )

    def _get(self, client=None, video=None):
        return (client or self.alice_client).get(f"/api/video/{(video or self.video).pk}/streams/")

    def test_returns_values_computed_from_the_files(self):
        self._stream("720p", [("index000.ts", 4.0, 1000), ("index001.ts", 4.0, 3000)])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "streams": [
                    {
                        "resolution": "720p",
                        "bitrate_kbps": 4,
                        "segment_count": 2,
                        "segment_seconds": 4,
                        "duration_seconds": 8.0,
                        "size_bytes": 4000,
                    }
                ]
            },
        )

    def test_old_upload_reports_the_target_duration_of_its_playlist(self):
        # Real old uploads (-hls_time 16) have a target of 17, 20 or 21, depending on the frame rate.
        self._stream("480p", [("index000.ts", 16.8, 10), ("index001.ts", 12.5, 10)], target=17)
        stream = self._get().json()["streams"][0]
        self.assertEqual(stream["segment_seconds"], 17)
        self.assertEqual(stream["duration_seconds"], 29.3)

    def _raw_stream(self, content, resolution="720p", segment_size=10):
        self._write(f"hls/clip/{resolution}/index000.ts", b"x" * segment_size)
        self._write(f"hls/clip/{resolution}/index.m3u8", content)
        VideoResolution.objects.create(
            original_video=self.video, resolution=resolution,
            converted_file=f"hls/clip/{resolution}/index.m3u8",
        )

    def test_non_finite_numbers_in_a_playlist_give_no_stream(self):
        cases = {
            "target inf": b"#EXT-X-TARGETDURATION:inf\n#EXTINF:4.0,\nindex000.ts\n",
            "target negative": b"#EXT-X-TARGETDURATION:-4\n#EXTINF:4.0,\nindex000.ts\n",
            "target zero": b"#EXT-X-TARGETDURATION:0\n#EXTINF:4.0,\nindex000.ts\n",
            "extinf nan": b"#EXT-X-TARGETDURATION:4\n#EXTINF:nan,\nindex000.ts\n",
            "extinf overflow": b"#EXT-X-TARGETDURATION:4\n#EXTINF:1e999,\nindex000.ts\n",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                VideoResolution.objects.all().delete()
                self._raw_stream(b"#EXTM3U\n" + body)
                response = self._get()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"streams": []})

    def test_broken_playlist_contents_give_no_stream(self):
        cases = {
            "broken target": b"#EXT-X-TARGETDURATION:abc\n#EXTINF:4.0,\nindex000.ts\n",
            "broken extinf": b"#EXT-X-TARGETDURATION:4\n#EXTINF:abc,\nindex000.ts\n",
            "nul byte in name": b"#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nindex\x00000.ts\n",
            "not utf-8": b"#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\n\xff\xfe\xfd.ts\n",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                VideoResolution.objects.all().delete()
                self._raw_stream(b"#EXTM3U\n" + body)
                response = self._get()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"streams": []})

    def test_playlist_without_target_duration_gives_no_stream(self):
        self._raw_stream(b"#EXTM3U\n#EXTINF:4.0,\nindex000.ts\n")
        self.assertEqual(self._get().json(), {"streams": []})

    def test_file_name_without_ts_extension_does_not_count(self):
        self._write("hls/clip/720p/index001.dat", b"d" * 500)
        self._raw_stream(
            b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nindex000.ts\n#EXTINF:4.0,\nindex001.dat\n",
            segment_size=1000,
        )
        stream = self._get().json()["streams"][0]
        self.assertEqual(stream["size_bytes"], 1000)
        self.assertEqual(stream["segment_count"], 1)

    def test_segment_linked_into_a_folder_with_the_same_prefix_is_not_counted(self):
        self._write("hls/clip/720p-evil/a.ts", b"s" * 5000)
        folder = os.path.join(self.media_root, "hls", "clip", "720p")
        os.makedirs(folder, exist_ok=True)
        try:
            os.symlink(
                os.path.join(self.media_root, "hls", "clip", "720p-evil", "a.ts"),
                os.path.join(folder, "index001.ts"),
            )
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self._raw_stream(
            b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nindex000.ts\n#EXTINF:4.0,\nindex001.ts\n"
        )
        self.assertEqual(self._get().json(), {"streams": []})

    def test_converted_file_outside_media_root_gives_an_empty_list(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with open(os.path.join(outside, "a.ts"), "wb") as f:
            f.write(b"x" * 10)
        with open(os.path.join(outside, "index.m3u8"), "wb") as f:
            f.write(b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\na.ts\n")
        VideoResolution.objects.create(
            original_video=self.video, resolution="720p",
            converted_file=os.path.relpath(os.path.join(outside, "index.m3u8"), self.media_root),
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"streams": []})

    def test_bitrate_is_kilobits_of_one_thousand(self):
        # 50000 bytes in 4 s are 100 kbit/s; dividing by 1024 would give 98.
        self._stream("720p", [("index000.ts", 4.0, 50000)])
        self.assertEqual(self._get().json()["streams"][0]["bitrate_kbps"], 100)

    def test_foreign_private_video_looks_like_a_missing_video(self):
        self._stream("720p", [("index000.ts", 4.0, 10)])
        foreign = self._get(client=self.bob_client)
        missing = self.bob_client.get("/api/video/999999/streams/")
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.content, missing.content)

    def test_streams_are_ordered_by_resolution_descending(self):
        for resolution in ["480p", "1080p", "720p"]:
            self._stream(resolution, [("index000.ts", 4.0, 10)])
        resolutions = [s["resolution"] for s in self._get().json()["streams"]]
        self.assertEqual(resolutions, ["1080p", "720p", "480p"])

    def test_foreign_private_video_is_404(self):
        self._stream("720p", [("index000.ts", 4.0, 10)])
        self.assertEqual(self._get(client=self.bob_client).status_code, 404)

    def test_standard_video_is_visible_to_any_account(self):
        standard = Video.objects.create(title="Standard", description="d", category="Action")
        self._stream("720p", [("index000.ts", 4.0, 10)], video=standard)
        response = self._get(client=self.bob_client, video=standard)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["streams"]), 1)

    def test_login_is_required(self):
        response = APIClient().get(f"/api/video/{self.video.pk}/streams/")
        self.assertIn(response.status_code, (401, 403))

    def test_video_without_resolutions_returns_empty_list(self):
        self.assertEqual(self._get().json(), {"streams": []})

    def test_missing_playlist_or_segment_skips_that_stream(self):
        self._stream("1080p", [("index000.ts", 4.0, 10)])
        self._stream("720p", [("index000.ts", 4.0, None)])  # segment missing on disk
        VideoResolution.objects.create(
            original_video=self.video, resolution="480p", converted_file="hls/clip/480p/index.m3u8"
        )  # playlist missing on disk
        VideoResolution.objects.create(original_video=self.video, resolution="360p", converted_file="")
        resolutions = [s["resolution"] for s in self._get().json()["streams"]]
        self.assertEqual(resolutions, ["1080p"])

    def test_playlist_without_duration_is_skipped(self):
        self._write("hls/clip/720p/index.m3u8", b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n")
        VideoResolution.objects.create(
            original_video=self.video, resolution="720p", converted_file="hls/clip/720p/index.m3u8"
        )
        self.assertEqual(self._get().json(), {"streams": []})

    def test_unfinished_conversion_reports_the_segments_so_far(self):
        self._stream("720p", [("index000.ts", 4.0, 100)])
        playlist = os.path.join(self.media_root, "hls/clip/720p/index.m3u8")
        with open(playlist, "rb") as f:
            content = f.read().replace(b"#EXT-X-ENDLIST", b"")
        with open(playlist, "wb") as f:
            f.write(content)
        self.assertEqual(self._get().json()["streams"][0]["segment_count"], 1)

    def test_segment_name_with_path_is_ignored(self):
        self._write("hls/secret.ts", b"s" * 5000)
        outside = os.path.join(self.media_root, "hls", "secret.ts")
        names = ["../../secret.ts", "..\\..\\secret.ts", outside, "/etc/passwd.ts", "http://host/a.ts", "sub/a.ts"]
        segments = [("index000.ts", 4.0, 1000)] + [(n, 4.0, None) for n in names]
        self._stream("720p", segments)
        stream = self._get().json()["streams"][0]
        self.assertEqual(stream["size_bytes"], 1000)
        self.assertEqual(stream["segment_count"], 1)
        self.assertEqual(stream["duration_seconds"], 4.0)

    def test_segment_that_is_a_link_to_a_file_outside_is_not_counted(self):
        self._write("hls/secret.ts", b"s" * 5000)
        folder = os.path.join(self.media_root, "hls", "clip", "720p")
        os.makedirs(folder, exist_ok=True)
        try:
            os.symlink(os.path.join(self.media_root, "hls", "secret.ts"), os.path.join(folder, "index001.ts"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self._stream("720p", [("index000.ts", 4.0, 1000)], lines=["#EXTINF:4.000000,", "index001.ts"])
        # The linked segment counts as missing, so the stream is skipped instead of leaking its size.
        self.assertEqual(self._get().json(), {"streams": []})
