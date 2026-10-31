import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

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
