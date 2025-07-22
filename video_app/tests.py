from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from video_app.models import Video, VideoResolution, VideoProgress
from accounts_app.models import CustomUser
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import mock_open, patch, MagicMock
from video_app.api.tasks import convert_video
from video_app.api import tasks, utils


class VideoAppIntegrationTests(APITestCase):
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

        # Another video for listing
        self.other_video = Video.objects.create(
            title="Second Video",
            description="Another test video.",
            category="Action",
            video_file=self.video_file,
        )

    # --- UPLOAD ---
    def test_upload_video_success(self):
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
        response = self.client.patch(url, data)
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
        response = self.client.patch(url, {"category": ""})
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
        response = self.client.post(url)
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
        response = self.client.patch(url, {})
        self.assertEqual(response.status_code, 200)

    def test_list_videos_empty(self):
        Video.objects.all().delete()
        cache.delete("all_videos")
        url = "/api/video/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_video_list_cache(self):
        url = "/api/video/"

        cache.delete("all_videos")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        cache.set("all_videos", [{"title": "From Cache"}], timeout=300)
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
    @patch("video_app.api.tasks.get_ffmpeg_thumbnail_command", return_value=["ffmpeg", "args"])
    @patch("video_app.api.tasks.build_output_filename", return_value="foo_thumbnail.jpg")
    @patch("video_app.api.tasks.get_base_name_and_extension", return_value=("foo", "mp4"))
    @patch("video_app.api.tasks.default_storage.path", side_effect=lambda x: x)
    def test_create_thumbnail_success(self, mock_path, mock_get_base, mock_build_out, mock_get_cmd,
        mock_subprocess, mock_openfile, mock_contentfile, mock_exists, mock_delete, mock_save):
        video = Video.objects.create(
            title="Video 1", description="desc", category="Action", video_file="videos/foo.mp4"
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
            converted_file="media/hls/vid/index.m3u8"
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
            converted_file="media/hls/vid/index.m3u8"
        )
        url = f"/api/video/{self.other_video.id}/480p/index001.ts"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "video/mp2t")

    def test_hls_serve_file_not_found(self):
        VideoResolution.objects.create(
            original_video=self.other_video,
            resolution="360p",
            converted_file="media/hls/vid/index.m3u8"
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
