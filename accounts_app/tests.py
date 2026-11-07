import base64
import os
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core import mail
from django.core.management import call_command
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework import status
from accounts_app.models import CustomUser
from django.utils import timezone
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from accounts_app.api.tasks import deliver_activation_email, deliver_password_reset_email
from accounts_app.api.utils import is_admin
from video_app.models import Video, VideoProgress, VideoResolution


def run_mail_jobs_inline(test_case):
    """
    Run queued mail jobs right away; tests need neither Redis nor a worker.
    """
    patcher = patch("accounts_app.api.tasks.django_rq.get_queue")
    get_queue = patcher.start()
    test_case.addCleanup(patcher.stop)
    get_queue.return_value.enqueue.side_effect = lambda task, *args: task(*args)
    return get_queue


class AccountsIntegrationTests(APITestCase):
    """
    Integration tests for the accounts_app.

    Covers registration, login, activation, password reset, deletion, and admin operations.
    """

    def setUp(self):
        # Throttle counters live in the cache (VFX-29); each test starts fresh.
        cache.clear()
        run_mail_jobs_inline(self)
        # Create admin
        self.admin = CustomUser.objects.create_user(
            username="admin",
            email="admin@test.de",
            password="Admin123!",
            is_staff=True,
            is_active=True,
        )

        # Create regular user (activated)
        self.user = CustomUser.objects.create_user(
            username="testuser",
            email="user@test.de",
            password="User123!",
            is_active=True,
        )

        # Create inactive user (not activated yet)
        self.inactive_user = CustomUser.objects.create_user(
            username="inactive",
            email="inactive@test.de",
            password="Inactive123!",
            is_active=False,
        )

        # Create a user for the admin to delete
        self.target_user = CustomUser.objects.create_user(
            username="target",
            email="target@test.de",
            password="Target123!",
            is_active=True,
        )

    def authenticate_with_cookies(self, email, password):
        response = self.client.post(
            "/api/login/", {"email": email, "password": password}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.client.cookies["access_token"] = response.cookies.get("access_token").value
        self.client.cookies["refresh_token"] = response.cookies.get(
            "refresh_token"
        ).value

    # --- REGISTRATION ---
    def test_registration_success(self):
        url = "/api/register/"
        data = {
            "email": "neu@example.com",
            "password": "NeuUser123!",
            "confirmed_password": "NeuUser123!",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(CustomUser.objects.filter(email="neu@example.com").exists())

    @override_settings(FRONTEND_URL="http://frontend.test")
    def test_activation_mail_links_to_frontend_route(self):
        data = {
            "email": "neu@example.com",
            "password": "NeuUser123!",
            "confirmed_password": "NeuUser123!",
        }
        self.client.post("/api/register/", data)
        user = CustomUser.objects.get(email="neu@example.com")
        uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f'href="http://frontend.test/activate/{uid}/{user.activation_code}"', html)

    @override_settings(FRONTEND_URL="http://frontend.test")
    def test_mail_logo_is_served_from_the_public_domain(self):
        data = {
            "email": "neu@example.com",
            "password": "NeuUser123!",
            "confirmed_password": "NeuUser123!",
        }
        self.client.post("/api/register/", data)
        html = mail.outbox[0].alternatives[0][0]
        # Gmail and Outlook do not show SVG images in mails (VFX-69).
        self.assertIn('src="http://frontend.test/static/img/logo.png"', html)
        self.assertNotIn("127.0.0.1", html)

    def test_registration_passwords_do_not_match(self):
        url = "/api/register/"
        data = {
            "email": "fail@example.com",
            "username": "failuser",
            "password": "pw1",
            "confirmed_password": "pw2",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Passwords do not match.", str(response.data))

    def test_registration_duplicate_email(self):
        url = "/api/register/"
        data = {
            "email": "user@test.de",
            "username": "anotheruser",
            "password": "Password123!",
            "confirmed_password": "Password123!",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", str(response.data).lower())

    # --- LOGIN ---
    def test_login_success(self):
        url = "/api/login/"
        data = {"email": "user@test.de", "password": "User123!"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.cookies)

    def test_login_wrong_password(self):
        url = "/api/login/"
        data = {"email": "user@test.de", "password": "falsch"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 400)

    def test_login_missing_fields(self):
        url = "/api/login/"
        data = {"email": "user@test.de"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 400)

    def test_login_inactive_user(self):
        url = "/api/login/"
        data = {"email": "inactive@test.de", "password": "Inactive123!"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 400)

    # --- ACTIVATION ---
    def get_uid_token(self, user):
        uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
        return uid, user.activation_code
    
    def test_activation_success(self):
        user = CustomUser.objects.create_user(
            username="activateuser",
            email="activate@test.de",
            password="Pw123456!",
            is_active=False,
        )
        user.activation_code = "testcode"
        user.activation_code_expiry = timezone.now() + timezone.timedelta(hours=1)
        user.save()
        uid, token = self.get_uid_token(user)
        url = f"/api/activate/{uid}/{token}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("detail") or response.data.get("message"), "Account successfully activated.")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNone(user.activation_code)

    def test_activation_expired(self):
        user = CustomUser.objects.create_user(
            username="expireduser",
            email="expired@test.de",
            password="Pw123456!",
            is_active=False,
        )
        user.activation_code = "expiredcode"
        user.activation_code_expiry = timezone.now() - timezone.timedelta(hours=1)
        user.save()
        uid, token = self.get_uid_token(user)
        url = f"/api/activate/{uid}/{token}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.data.get("detail", "").lower() or response.data.get("message", "").lower())
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_activation_invalid_code(self):
        user = CustomUser.objects.create_user(
            username="testuser_invalidcode",
            email="testuser_invalidcode@test.de",
            password="Pw123456!",
            is_active=False,
        )
        user.activation_code = "validcode"
        user.activation_code_expiry = timezone.now() + timezone.timedelta(hours=1)
        user.save()
        uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
        invalid_token = "thiscodedoesnotexist"
        url = f"/api/activate/{uid}/{invalid_token}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid", response.data.get("detail", "").lower() or response.data.get("message", "").lower())


    # --- REQUEST NEW ACTIVATION LINK ---
    def test_request_new_activation_link_success(self):
        CustomUser.objects.create_user(
            username="newactivation",
            email="newactivation@test.de",
            password="pw",
            is_active=False,
        )
        url = "/api/request-new-activation-link/"
        data = {"email": "newactivation@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("detail", response.data)

    def test_request_new_activation_link_nonexistent(self):
        url = "/api/request-new-activation-link/"
        data = {"email": "notfound@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)

    # --- PASSWORD RESET ---
    def test_password_reset_request_success(self):
        url = "/api/password_reset/"
        data = {"email": "user@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)

    @override_settings(FRONTEND_URL="http://frontend.test")
    def test_password_reset_mail_links_to_frontend_route(self):
        self.client.post("/api/password_reset/", {"email": "user@test.de"})
        self.user.refresh_from_db()
        uid = base64.urlsafe_b64encode(str(self.user.pk).encode()).decode()
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f'href="http://frontend.test/reset-password/{uid}/{self.user.activation_code}"', html)

    def test_password_reset_request_nonexistent(self):
        url = "/api/password_reset/"
        data = {"email": "idontexist@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)

    # --- PASSWORD RESET CONFIRM ---
    def test_password_reset_confirm_success(self):
        user = CustomUser.objects.create_user(
            username="pwreset",
            email="pwreset@test.de",
            password="OldPw123!",
            is_active=True,
        )
        user.activation_code = "resetcode"
        user.activation_code_expiry = timezone.now() + timezone.timedelta(hours=1)
        user.save()
        uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
        url = f"/api/password_confirm/{uid}/resetcode/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPw123!"))
        self.assertIsNone(user.activation_code)

    def test_password_reset_confirm_expired(self):
        user = CustomUser.objects.create_user(
            username="pwexpired",
            email="pwexpired@test.de",
            password="OldPw123!",
            is_active=True,
        )
        user.activation_code = "expiredpwcode"
        user.activation_code_expiry = timezone.now() - timezone.timedelta(hours=1)
        user.save()
        uid = user.pk
        url = f"/api/password_confirm/{uid}/expiredpwcode/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    def test_password_reset_confirm_invalid_code(self):
        url = "/api/password_confirm/9999/invalidcode/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    # --- HARD DELETE (ADMIN) ---
    def test_hard_delete_success(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = f"/api/admin/delete-account/{self.target_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.target_user.pk).exists())

    def test_hard_delete_not_admin(self):
        self.authenticate_with_cookies("user@test.de", "User123!")
        url = f"/api/admin/delete-account/{self.target_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 403)

    def test_hard_delete_no_auth(self):
        url = f"/api/admin/delete-account/{self.target_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 401)

    def test_hard_delete_invalid_user(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = "/api/admin/delete-account/99999/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)

    def test_restore_route_is_gone(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        response = self.client.post(f"/api/admin/restore-account/{self.target_user.pk}/")
        self.assertEqual(response.status_code, 404)


class DeleteOwnAccountTests(APITestCase):
    """
    DELETE /api/delete-account/ deletes at once and for good, as the privacy policy promises
    (X5 finding 6, decision F2): rows and files go, and no way brings the account back.
    """

    url = "/api/delete-account/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        run_mail_jobs_inline(self)
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=self.media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        queue = patch("video_app.api.signals.django_rq.get_queue")
        queue.start()
        self.addCleanup(queue.stop)
        self.user = CustomUser.objects.create_user(
            username="user", email="user@test.de", password="User123!", is_active=True
        )
        response = self.client.post(
            "/api/login/", {"email": "user@test.de", "password": "User123!"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.old_access = response.cookies["access_token"].value
        self.old_refresh = response.cookies["refresh_token"].value

    def _write(self, relative_path):
        path = os.path.join(self.media, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"data")
        return relative_path

    def _delete(self):
        response = self.client.delete(self.url, {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 204)
        return response

    def test_rows_and_files_are_gone(self):
        video = Video.objects.create(
            title="Mine", description="d", category="Comedy", owner=self.user,
            video_file=self._write("videos/mine.mp4"),
            thumbnail_url=self._write("thumbnails/mine.jpg"),
        )
        VideoResolution.objects.create(
            original_video=video, resolution="480p",
            converted_file=self._write(f"hls/{video.pk}/480p/index.m3u8"),
        )
        VideoProgress.objects.create(user=self.user, video=video, progress_seconds=5)
        self._delete()
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Video.objects.filter(pk=video.pk).exists())
        self.assertFalse(VideoResolution.objects.exists())
        self.assertFalse(VideoProgress.objects.exists())
        for folder in ["videos", "thumbnails", "hls"]:
            with self.subTest(folder=folder):
                folder_path = os.path.join(self.media, folder)
                self.assertEqual(os.listdir(folder_path) if os.path.isdir(folder_path) else [], [])

    def test_other_accounts_keep_their_videos(self):
        other = CustomUser.objects.create_user(
            username="other", email="other@test.de", password="Other123!", is_active=True
        )
        path = self._write("videos/other.mp4")
        video = Video.objects.create(
            title="Theirs", description="d", category="Comedy", owner=other, video_file=path
        )
        self._delete()
        self.assertTrue(Video.objects.filter(pk=video.pk).exists())
        self.assertTrue(os.path.exists(os.path.join(self.media, path)))

    def test_auth_cookies_are_cleared(self):
        response = self._delete()
        for name in ["access_token", "refresh_token"]:
            with self.subTest(cookie=name):
                self.assertEqual(response.cookies[name].value, "")
                self.assertEqual(response.cookies[name]["max-age"], 0)

    def test_old_tokens_stop_working(self):
        self._delete()
        client = APIClient()
        client.cookies["refresh_token"] = self.old_refresh
        self.assertEqual(client.post("/api/token/refresh/").status_code, 401)
        client = APIClient()
        client.cookies["access_token"] = self.old_access
        self.assertEqual(client.get("/api/me/").status_code, 401)

    def test_old_activation_link_does_not_revive_the_account(self):
        self.user.activation_code = "oldcode"
        self.user.activation_code_expiry = timezone.now() + timedelta(hours=1)
        self.user.save()
        uid = base64.urlsafe_b64encode(str(self.user.pk).encode()).decode()
        self._delete()
        response = APIClient().get(f"/api/activate/{uid}/oldcode/")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CustomUser.objects.filter(email="user@test.de").exists())

    def test_new_link_and_reset_requests_send_nothing(self):
        self._delete()
        for url in ["/api/request-new-activation-link/", "/api/password_reset/"]:
            with self.subTest(url=url):
                response = APIClient().post(url, {"email": "user@test.de"}, format="json")
                self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(CustomUser.objects.filter(email="user@test.de").exists())

    def test_address_is_free_again(self):
        self._delete()
        response = APIClient().post(
            "/api/register/",
            {"email": "user@test.de", "password": "NeuUser123!", "confirmed_password": "NeuUser123!"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(CustomUser.objects.get(email="user@test.de").is_active)

    def test_without_confirmation_nothing_happens(self):
        response = self.client.delete(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_without_login_nothing_happens(self):
        response = APIClient().delete(self.url, {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_swagger_describes_the_real_behaviour(self):
        response = self.client.get("/swagger/?format=openapi")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("permanently", paths["/delete-account/"]["delete"]["description"])
        self.assertNotIn("soft", str(paths["/delete-account/"]).lower())
        self.assertFalse([path for path in paths if "restore" in path])


class EmailCaseTests(APITestCase):
    """
    The case of an address must neither lock anyone out nor create a second account (X4 finding 2).

    New addresses are stored lower-case. Older rows keep their spelling, and two of them may
    differ only by case: the exact spelling wins, then the oldest row.
    """

    password = "Secret123!"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        run_mail_jobs_inline(self)

    def _register(self, email):
        return self.client.post(
            "/api/register/",
            {"email": email, "password": self.password, "confirmed_password": self.password},
        )

    def _user(self, email, password=None, is_active=True):
        return CustomUser.objects.create_user(
            username=email, email=email, password=password or self.password, is_active=is_active
        )

    def _login(self, email, password=None):
        return self.client.post(
            "/api/login/", {"email": email, "password": password or self.password}, format="json"
        )

    def _me(self):
        return self.client.get("/api/me/").data["email"]

    def _expire_link(self, user):
        CustomUser.objects.filter(pk=user.pk).update(
            activation_code_expiry=timezone.now() - timedelta(hours=1)
        )

    # --- REGISTRATION ---
    def test_registration_stores_the_address_lower_case(self):
        self.assertEqual(self._register("Max.Muster@Example.COM").status_code, 201)
        self.assertEqual(CustomUser.objects.get().email, "max.muster@example.com")

    def test_second_registration_in_any_case_is_rejected(self):
        self._register("Max.Muster@Example.COM")
        for email in ["Max.Muster@Example.COM", "max.muster@example.com", "MAX.MUSTER@EXAMPLE.COM"]:
            with self.subTest(email=email):
                response = self._register(email)
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.data)
        self.assertEqual(CustomUser.objects.count(), 1)

    def test_registration_is_rejected_when_an_older_row_differs_by_case(self):
        self._user("Old.Row@example.com")
        for email in ["old.row@example.com", "OLD.ROW@EXAMPLE.COM", "Old.Row@example.com"]:
            with self.subTest(email=email):
                self.assertEqual(self._register(email).status_code, 400)
        self.assertEqual(CustomUser.objects.count(), 1)

    # --- LOGIN ---
    def test_login_works_with_the_spelling_used_at_registration(self):
        self._register("Max.Muster@Example.COM")
        CustomUser.objects.update(is_active=True)
        response = self._login("Max.Muster@Example.COM")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.cookies)

    def test_login_finds_an_older_row_in_any_case(self):
        self._user("Old.Row@example.com")
        for email in ["old.row@example.com", "OLD.ROW@EXAMPLE.COM", "Old.Row@example.com"]:
            with self.subTest(email=email):
                cache.clear()
                self.assertEqual(self._login(email).status_code, 200)

    def test_login_with_the_wrong_password_stays_rejected(self):
        self._user("user@example.com")
        self.assertEqual(self._login("USER@example.com", password="wrong").status_code, 400)

    def test_login_with_two_rows_prefers_the_exact_spelling(self):
        self._user("Twin@example.com", password="First123!")
        self._user("twin@example.com", password="Second123!")
        self.assertEqual(self._login("twin@example.com", password="Second123!").status_code, 200)
        self.assertEqual(self._me(), "twin@example.com")

    def test_login_with_two_rows_and_another_spelling_takes_the_oldest(self):
        self._user("Twin@example.com", password="First123!")
        self._user("twin@example.com", password="Second123!")
        self.assertEqual(self._login("TWIN@example.com", password="First123!").status_code, 200)
        self.assertEqual(self._me(), "Twin@example.com")
        self.assertEqual(self._login("TWIN@example.com", password="Second123!").status_code, 400)

    # --- PASSWORD RESET ---
    def test_reset_finds_the_account_in_any_case_and_mails_the_stored_address(self):
        self._user("Old.Row@example.com")
        response = self.client.post("/api/password_reset/", {"email": "OLD.ROW@EXAMPLE.COM"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["Old.Row@example.com"]])

    def test_reset_with_two_rows_sends_one_mail(self):
        self._user("Twin@example.com")
        self._user("twin@example.com")
        response = self.client.post("/api/password_reset/", {"email": "TWIN@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["Twin@example.com"]])

    def test_reset_skips_an_inactive_row(self):
        self._user("Twin@example.com", is_active=False)
        self._user("twin@example.com")
        self.client.post("/api/password_reset/", {"email": "TWIN@example.com"})
        self.assertEqual([m.to for m in mail.outbox], [["twin@example.com"]])

    # --- NEW ACTIVATION LINK ---
    def test_new_link_finds_the_account_in_any_case(self):
        self._user("Old.Row@example.com", is_active=False)
        response = self.client.post("/api/request-new-activation-link/", {"email": "OLD.ROW@EXAMPLE.COM"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["Old.Row@example.com"]])

    def test_new_link_with_two_rows_sends_one_mail(self):
        self._user("Twin@example.com", is_active=False)
        self._user("twin@example.com", is_active=False)
        response = self.client.post("/api/request-new-activation-link/", {"email": "twin@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["twin@example.com"]])

    def test_new_link_keeps_a_link_that_is_still_valid(self):
        self._register("user@example.com")
        mail.outbox.clear()
        self.client.post("/api/request-new-activation-link/", {"email": "USER@example.com"})
        self.assertEqual(mail.outbox, [])
        self._expire_link(CustomUser.objects.get())
        self.client.post("/api/request-new-activation-link/", {"email": "USER@example.com"})
        self.assertEqual(len(mail.outbox), 1)

    def test_new_link_without_a_usable_address_stays_neutral(self):
        for data in [{}, {"email": ["user@example.com"]}, {"email": 5}]:
            with self.subTest(data=data):
                cache.clear()
                response = self.client.post("/api/request-new-activation-link/", data, format="json")
                self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])


class MailWorkerTests(APITestCase):
    """
    Mails leave through the worker, so an SMTP or Redis outage never fails the request (VFX-52).
    """

    REGISTRATION = {
        "email": "neu@example.com",
        "password": "NeuUser123!",
        "confirmed_password": "NeuUser123!",
    }

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            username="user", email="user@test.de", password="User123!", is_active=True
        )

    @patch("accounts_app.api.tasks.django_rq.get_queue")
    def test_registration_queues_the_mail_with_the_user_id_only(self, get_queue):
        response = self.client.post("/api/register/", self.REGISTRATION)
        self.assertEqual(response.status_code, 201)
        user = CustomUser.objects.get(email="neu@example.com")
        get_queue.assert_called_once_with("mail", autocommit=True)
        get_queue.return_value.enqueue.assert_called_once_with(deliver_activation_email, user.pk)
        self.assertEqual(len(mail.outbox), 0)

    @patch("accounts_app.api.tasks.django_rq.get_queue")
    def test_password_reset_queues_the_mail_with_the_user_id_only(self, get_queue):
        response = self.client.post("/api/password_reset/", {"email": "user@test.de"})
        self.assertEqual(response.status_code, 200)
        get_queue.assert_called_once_with("mail", autocommit=True)
        get_queue.return_value.enqueue.assert_called_once_with(deliver_password_reset_email, self.user.pk)
        self.assertEqual(len(mail.outbox), 0)

    @patch("accounts_app.api.tasks.django_rq.get_queue", side_effect=ConnectionError("redis down"))
    def test_registration_survives_a_redis_outage(self, _):
        with self.assertLogs("accounts_app.api.tasks", level="ERROR"):
            response = self.client.post("/api/register/", self.REGISTRATION)
        self.assertEqual(response.status_code, 201)

    @patch("accounts_app.api.tasks.django_rq.get_queue", side_effect=ConnectionError("redis down"))
    def test_password_reset_survives_a_redis_outage(self, _):
        with self.assertLogs("accounts_app.api.tasks", level="ERROR"):
            response = self.client.post("/api/password_reset/", {"email": "user@test.de"})
        self.assertEqual(response.status_code, 200)

    def test_worker_sends_the_activation_mail(self):
        self.user.activation_code = "code"
        self.user.save()
        deliver_activation_email(self.user.pk)
        self.assertEqual(mail.outbox[0].to, ["user@test.de"])

    def test_worker_sends_the_reset_mail(self):
        self.user.activation_code = "code"
        self.user.save()
        deliver_password_reset_email(self.user.pk)
        self.assertEqual(mail.outbox[0].to, ["user@test.de"])

    @patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down user@test.de"))
    def test_smtp_outage_is_logged_without_the_address(self, _):
        with self.assertLogs("accounts_app.api.tasks", level="ERROR") as logs:
            deliver_activation_email(self.user.pk)
        self.assertNotIn("user@test.de", "".join(logs.output))

    def test_deleted_account_is_skipped(self):
        user_id = self.user.pk
        self.user.delete()
        deliver_password_reset_email(user_id)
        self.assertEqual(len(mail.outbox), 0)


class MeEndpointTests(APITestCase):
    """
    /api/me/ returns the logged-in account and its role (VFX-26).
    """

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username="me_admin", email="me-admin@test.de", password="pw", is_active=True, is_staff=True
        )
        self.member = CustomUser.objects.create_user(
            username="me_member", email="me-member@test.de", password="pw", is_active=True
        )

    def test_me_requires_login(self):
        self.assertEqual(self.client.get("/api/me/").status_code, 401)

    def test_me_returns_member_role(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "me-member@test.de")
        self.assertEqual(response.data["role"], "member")

    def test_me_returns_admin_role(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/me/")
        self.assertEqual(response.data["role"], "admin")


class IsAdminHelperTests(TestCase):
    """
    is_admin(user) is the single place that decides the admin role (VFX-26).
    """

    def test_staff_account_is_admin(self):
        user = CustomUser(username="helper_admin", email="helper-admin@test.de", is_staff=True)
        self.assertTrue(is_admin(user))

    def test_member_is_not_admin(self):
        user = CustomUser(username="helper_member", email="helper-member@test.de")
        self.assertFalse(is_admin(user))

    def test_anonymous_user_is_not_admin(self):
        self.assertFalse(is_admin(AnonymousUser()))


class CookieAndCsrfTests(APITestCase):
    """
    JWT cookies and CSRF protection (VFX-07, masterplan E6).

    Cookies are SameSite=Strict. Requests authenticated by cookie must also send the
    CSRF token; requests with an Authorization header cannot be forged and need none.
    """

    def setUp(self):
        cache.clear()
        self.password = "Secret123!"
        self.user = CustomUser.objects.create_user(
            username="csrf_user", email="csrf@test.de", password=self.password, is_active=True
        )
        self.client = APIClient(enforce_csrf_checks=True)

    def _login(self):
        response = self.client.post(
            "/api/login/", {"email": self.user.email, "password": self.password}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        return response

    def _delete_own_account(self, **extra):
        return self.client.delete("/api/delete-account/", {"confirm": True}, format="json", **extra)

    # --- COOKIES ---
    def test_login_sets_strict_secure_httponly_cookies(self):
        response = self._login()
        for name in ["access_token", "refresh_token"]:
            with self.subTest(cookie=name):
                cookie = response.cookies[name]
                self.assertEqual(cookie["samesite"], "Strict")
                self.assertTrue(cookie["secure"])
                self.assertTrue(cookie["httponly"])

    def test_login_sets_csrf_cookie(self):
        response = self._login()
        self.assertTrue(response.cookies["csrftoken"].value)

    def test_refresh_sets_strict_access_cookie(self):
        self._login()
        response = self.client.post("/api/token/refresh/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["access_token"]["samesite"], "Strict")

    def test_login_works_with_existing_cookie_and_without_csrf_token(self):
        self._login()
        self._login()

    def test_login_rejects_form_post(self):
        # Login CSRF: a foreign page can submit an HTML form but cannot send JSON cross-site.
        response = self.client.post(
            "/api/login/",
            {"email": self.user.email, "password": self.password},
            format="multipart",
        )
        self.assertEqual(response.status_code, 415)
        self.assertNotIn("access_token", response.cookies)

    def test_login_rejects_urlencoded_post(self):
        response = self.client.post(
            "/api/login/",
            f"email={self.user.email}&password={self.password}",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 415)
        self.assertNotIn("access_token", response.cookies)

    # --- CSRF ---
    def test_cookie_request_without_csrf_token_is_rejected(self):
        self._login()
        response = self._delete_own_account()
        self.assertEqual(response.status_code, 403)
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_cookie_request_with_csrf_token_is_accepted(self):
        token = self._login().cookies["csrftoken"].value
        response = self._delete_own_account(HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 204)

    def test_safe_cookie_request_needs_no_csrf_token(self):
        self._login()
        self.assertEqual(self.client.get("/api/me/").status_code, 200)

    def test_header_auth_needs_no_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        self.assertEqual(client.post("/api/logout/").status_code, 200)


class ThrottlingTests(APITestCase):
    """
    Rate limits for anonymous auth endpoints (VFX-29).

    Login: 5 per minute per IP. Endpoints that send mail share 5 per hour per IP.
    The client IP is only taken from X-Forwarded-For when a trusted proxy is configured.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _login(self, **extra):
        return self.client.post(
            "/api/login/", {"email": "nobody@test.de", "password": "wrong"}, format="json", **extra
        )

    def test_login_is_limited_per_ip(self):
        for _ in range(5):
            self.assertEqual(self._login().status_code, 400)
        self.assertEqual(self._login().status_code, 429)

    def test_login_limit_is_per_ip(self):
        for _ in range(5):
            self._login(REMOTE_ADDR="10.0.0.1")
        self.assertEqual(self._login(REMOTE_ADDR="10.0.0.2").status_code, 400)

    def test_spoofed_forwarded_for_does_not_bypass_limit(self):
        for i in range(5):
            self._login(HTTP_X_FORWARDED_FOR=f"203.0.113.{i}")
        self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.99").status_code, 429)

    def test_behind_one_proxy_the_forwarded_client_ip_counts(self):
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}):
            for _ in range(5):
                self._login(HTTP_X_FORWARDED_FOR="203.0.113.1")
            self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.1").status_code, 429)
            self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.2").status_code, 400)

    def test_mail_endpoints_share_an_hourly_limit(self):
        requests = [
            ("/api/password_reset/", {"email": "nobody@test.de"}),
            ("/api/request-new-activation-link/", {"email": "nobody@test.de"}),
            ("/api/password_reset/", {"email": "nobody@test.de"}),
            ("/api/request-new-activation-link/", {"email": "nobody@test.de"}),
            ("/api/register/", {"email": "invalid"}),
        ]
        for url, data in requests:
            self.assertNotEqual(self.client.post(url, data, format="json").status_code, 429)
        for url, data in requests:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data, format="json").status_code, 429)


@override_settings(
    # Admin pages need static files; the manifest only exists after collectstatic (prod image).
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class AdminLoginThrottlingTests(APITestCase):
    """
    The Django admin login, also used by the RQ dashboard, has the same limit as the API login
    (X5 finding 3): 5 POST per minute per IP, with one counter for both.
    """

    url = "/admin/login/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _login(self, password="wrong", **extra):
        return self.client.post(self.url, {"username": "root@test.de", "password": password}, **extra)

    def test_login_is_limited_per_ip(self):
        for _ in range(5):
            self.assertEqual(self._login().status_code, 200)
        response = self._login()
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response["Retry-After"])

    def test_limit_is_per_ip(self):
        for _ in range(5):
            self._login(REMOTE_ADDR="10.0.0.1")
        self.assertEqual(self._login(REMOTE_ADDR="10.0.0.2").status_code, 200)

    def test_spoofed_forwarded_for_does_not_bypass_limit(self):
        for i in range(5):
            self._login(HTTP_X_FORWARDED_FOR=f"203.0.113.{i}")
        self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.99").status_code, 429)

    def test_behind_one_proxy_the_forwarded_client_ip_counts(self):
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}):
            for _ in range(5):
                self._login(HTTP_X_FORWARDED_FOR="203.0.113.1")
            self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.1").status_code, 429)
            self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.2").status_code, 200)

    def test_over_the_limit_no_password_is_checked(self):
        with patch("django.contrib.auth.forms.authenticate", return_value=None) as authenticate:
            for _ in range(5):
                self._login()
            self.assertEqual(authenticate.call_count, 5)
            self.assertEqual(self._login().status_code, 429)
            self.assertEqual(authenticate.call_count, 5)

    def test_get_stays_free(self):
        for _ in range(6):
            self._login()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_api_login_and_admin_login_share_one_counter(self):
        for _ in range(5):
            self.client.post(
                "/api/login/", {"email": "nobody@test.de", "password": "wrong"}, format="json"
            )
        self.assertEqual(self._login().status_code, 429)

    def test_staff_can_still_log_in(self):
        CustomUser.objects.create_superuser(username="root", email="root@test.de", password="Root123!")
        response = self._login(password="Root123!")
        self.assertEqual(response.status_code, 302)


class DemoAccountTests(APITestCase):
    """
    Demo accounts (VFX-28, masterplan E11, G2, G5, G6).

    One click, no mail, member rights, expires after 2 hours. At most 5 demos per hour
    per IP and 20 active at once; a cleanup command hard-deletes expired demos with
    their uploads.
    """

    url = "/api/demo/"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _start(self, client=None, **extra):
        return (client or self.client).post(self.url, {}, format="json", **extra)

    def _demo(self, age=timedelta(0)):
        email = f"demo-{CustomUser.objects.count()}@demo.invalid"
        user = CustomUser.objects.create_user(
            username=email, email=email, is_active=True, is_demo=True
        )
        CustomUser.objects.filter(pk=user.pk).update(date_joined=timezone.now() - age)
        user.refresh_from_db()
        return user

    # --- START ---
    def test_start_creates_demo_and_logs_in(self):
        response = self._start()
        self.assertEqual(response.status_code, 201)
        for name in ["access_token", "refresh_token", "csrftoken"]:
            self.assertTrue(response.cookies[name].value)
        me = self.client.get("/api/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["role"], "demo")
        user = CustomUser.objects.get(pk=me.data["id"])
        self.assertTrue(user.is_demo)
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_demo_cookies_expire_with_the_demo(self):
        response = self._start()
        self.assertLessEqual(int(response.cookies["refresh_token"]["max-age"]), 2 * 60 * 60)

    def test_start_sends_no_mail(self):
        self._start()
        self.assertEqual(len(mail.outbox), 0)

    def test_start_rejects_form_post(self):
        # Like login CSRF: a foreign page must not log visitors into a demo account.
        for content_type in ["application/x-www-form-urlencoded", "multipart/form-data", "text/plain"]:
            with self.subTest(content_type=content_type):
                response = self.client.post(self.url, "", content_type=content_type)
                self.assertEqual(response.status_code, 415)
        self.assertFalse(CustomUser.objects.filter(is_demo=True).exists())

    def test_demo_cannot_log_in_with_password(self):
        self._start()
        user = CustomUser.objects.get(is_demo=True)
        response = self.client.post(
            "/api/login/", {"email": user.email, "password": ""}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    # --- LIMITS ---
    def test_start_is_limited_per_ip(self):
        for _ in range(5):
            self.assertEqual(self._start(APIClient()).status_code, 201)
        self.assertEqual(self._start(APIClient()).status_code, 429)

    def test_at_most_20_active_demos(self):
        for _ in range(20):
            self._demo()
        response = self._start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(CustomUser.objects.filter(is_demo=True).count(), 20)

    def test_expired_demos_do_not_count_as_active(self):
        for _ in range(20):
            self._demo(age=timedelta(hours=3))
        self.assertEqual(self._start().status_code, 201)

    # --- EXPIRY ---
    def test_expired_demo_is_rejected_before_cleanup(self):
        user = self._demo(age=timedelta(hours=2, minutes=1))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        self.assertEqual(client.get("/api/me/").status_code, 401)

    def test_fresh_demo_is_accepted(self):
        user = self._demo(age=timedelta(hours=1, minutes=59))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        self.assertEqual(client.get("/api/me/").status_code, 200)

    # --- CLEANUP ---
    def test_cleanup_deletes_only_expired_demos(self):
        expired = self._demo(age=timedelta(hours=3))
        fresh = self._demo(age=timedelta(hours=1))
        old_member = CustomUser.objects.create_user(
            username="member", email="member@test.de", is_active=True
        )
        CustomUser.objects.filter(pk=old_member.pk).update(
            date_joined=timezone.now() - timedelta(days=30)
        )
        out = StringIO()
        call_command("delete_expired_demos", stdout=out)
        self.assertFalse(CustomUser.objects.filter(pk=expired.pk).exists())
        self.assertTrue(CustomUser.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(CustomUser.objects.filter(pk=old_member.pk).exists())
        self.assertIn("1", out.getvalue())


class RefreshInactiveAccountTests(APITestCase):
    """
    A refresh token must not outlive the account it belongs to (VFX-53).
    """

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            username="user", email="user@test.de", password="User123!", is_active=True
        )
        self.client.cookies["refresh_token"] = str(RefreshToken.for_user(self.user))

    def refresh(self):
        return self.client.post("/api/token/refresh/")

    def test_active_account_gets_a_new_access_token(self):
        response = self.refresh()
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.cookies)

    def test_deactivated_account_gets_no_new_access_token(self):
        CustomUser.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.refresh()
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.cookies)

    def test_deleted_account_gets_no_new_access_token(self):
        self.user.delete()
        response = self.refresh()
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.cookies)
