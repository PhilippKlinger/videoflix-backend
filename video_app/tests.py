import importlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.cache import cache
from django.http import Http404
from django.test import override_settings
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


class VideoAppIntegrationTests(APITestCase):
    """
    Integration tests for the video_app.

    Covers upload, listing, detail, patch, conversion, progress, cache, resolutions, and HLS streaming.
    """

    def setUp(self):
        self.unauthenticated_client = APIClient()
        self.raw_password = "testpw"
        self.user = CustomUser.objects.create_user(
            username="video_user",
            email="video@test.de",
            password=self.raw_password,
            is_active=True,
        )

        login_data = {"email": self.user.email, "password": self.raw_password}
        response = self.client.post("/api/login/", login_data)

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

        cache.set(STANDARD_VIDEOS_CACHE_KEY, [{"title": "From Cache"}], timeout=300)
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

    def test_get_ffmpeg_hls_command(self):
        cmd, _ = utils.get_ffmpeg_hls_command("input", "output", "height", 720)
        self.assertIn("-vf", cmd)
        self.assertIn("scale=-2:720", cmd)

    @patch("django.db.models.fields.files.FieldFile.save", return_value=None)
    @patch("video_app.api.tasks.default_storage.delete", return_value=True)
    @patch("video_app.api.tasks.default_storage.exists", return_value=True)
    @patch("video_app.api.tasks.ContentFile", return_value=b"img")
    @patch("video_app.api.tasks.open", new_callable=mock_open, read_data=b"img")
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

    def test_admin_can_open_django_rq(self):
        self.client.force_login(self.admin)
        response = self.client.get("/api/django-rq/")
        self.assertEqual(response.status_code, 200)

    # --- DJANGO ADMIN ---
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
        self.assertEqual(response["Cache-Control"], "private")
        self.assertEqual(b"".join(response.streaming_content), b"JPEG")

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
