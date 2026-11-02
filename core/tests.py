import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from core.json_logging import JsonFormatter
from video_app.models import Video

BASE_DIR = Path(__file__).resolve().parent.parent


def load_settings(**overrides):
    """Import core.settings in a fresh interpreter with the given environment.

    Empty strings are passed on purpose: load_dotenv() never overrides existing
    variables, so a local .env cannot mask a missing value.
    """
    env = {**os.environ, "SECRET_KEY": "test-only-key", "DB_PASSWORD": "test-only-password"}
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import core.settings"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


class SecretSettingsTests(SimpleTestCase):
    def test_settings_load_when_secrets_are_set(self):
        result = load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_db_password_aborts_startup(self):
        result = load_settings(DB_PASSWORD="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_PASSWORD must be set", result.stderr)


class HealthEndpointTests(APITestCase):
    """
    /api/health/ for Docker and the edge proxy (VFX-31).

    No login, no throttling, no redirect to HTTPS; failures are reported
    without technical details (those go to the log).
    """

    url = "/api/health/"

    def test_reports_ok_without_login(self):
        response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok", "cache": "ok"})

    def test_database_failure_returns_503_without_details(self):
        with patch("core.health.connection.cursor", side_effect=OperationalError("db host secret")):
            with self.assertLogs("core.health", level="ERROR"):
                response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "error")
        self.assertNotIn("secret", response.content.decode())

    def test_cache_failure_returns_503_without_details(self):
        with patch("core.health.cache.set", side_effect=ConnectionError("redis host secret")):
            with self.assertLogs("core.health", level="ERROR"):
                response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["cache"], "error")
        self.assertNotIn("secret", response.content.decode())

    def test_is_not_redirected_to_https(self):
        with override_settings(SECURE_SSL_REDIRECT=True):
            client = APIClient()
            self.assertEqual(client.get(self.url).status_code, 200)
            self.assertEqual(client.get("/api/video/").status_code, 301)

    def test_is_not_throttled(self):
        client = APIClient()
        for _ in range(30):
            self.assertEqual(client.get(self.url).status_code, 200)


class JsonLogFormatterTests(SimpleTestCase):
    """
    One JSON object per log line, without personal data or secrets (VFX-31).
    """

    def _format(self, msg, *args, exc_info=None):
        record = logging.LogRecord("video_app", logging.INFO, __file__, 1, msg, args, exc_info)
        return json.loads(JsonFormatter().format(record))

    def test_writes_one_json_object(self):
        entry = self._format("Video %s saved", 7)
        self.assertEqual(entry["level"], "INFO")
        self.assertEqual(entry["logger"], "video_app")
        self.assertEqual(entry["message"], "Video 7 saved")
        self.assertIn("time", entry)

    def test_includes_exception(self):
        try:
            raise ValueError("boom")
        except ValueError:
            entry = self._format("failed", exc_info=sys.exc_info())
        self.assertIn("ValueError: boom", entry["exception"])

    def test_redacts_tokens_in_account_links(self):
        for path in [
            "Bad Request: /api/activate/MTI/abc-123token/",
            "Bad Request: /api/password_confirm/MTI/abc-123token/",
        ]:
            with self.subTest(path=path):
                message = self._format(path)["message"]
                self.assertNotIn("abc-123token", message)
                self.assertNotIn("MTI", message)


class VideoLogPrivacyTests(APITestCase):
    """
    Video logs name the ID, never the title or file name (VFX-31, /secure S4).
    """

    def test_saving_a_video_logs_no_title(self):
        with patch("video_app.api.signals.django_rq.get_queue"):
            with self.assertLogs("video_app.api.signals", level="INFO") as logs:
                video = Video.objects.create(
                    title="Private Holiday", description="d", category="Comedy"
                )
        output = "\n".join(logs.output)
        self.assertIn(str(video.pk), output)
        self.assertNotIn("Private Holiday", output)
