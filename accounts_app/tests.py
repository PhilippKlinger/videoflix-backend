import base64
from datetime import timedelta
from io import StringIO
from django.core import mail
from django.core.management import call_command
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework import status
from accounts_app.models import CustomUser
from django.utils import timezone
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from accounts_app.api.utils import is_admin


class AccountsIntegrationTests(APITestCase):
    """
    Integration tests for the accounts_app.

    Covers registration, login, activation, password reset, deletion, and admin operations.
    """

    def setUp(self):
        # Throttle counters live in the cache (VFX-29); each test starts fresh.
        cache.clear()
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

        # Create soft-deleted user
        self.deleted_user = CustomUser.objects.create_user(
            username="deleted",
            email="deleted@test.de",
            password="Deleted123!",
            is_active=False,
            is_soft_deleted=True,
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

    def test_login_soft_deleted_user(self):
        url = "/api/login/"
        data = {"email": "deleted@test.de", "password": "Deleted123!"}
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

    # --- SOFT DELETE ---
    def test_soft_delete_success(self):
        self.authenticate_with_cookies("user@test.de", "User123!")
        url = "/api/delete-account/"
        data = {"confirm": True}
        response = self.client.delete(url, data)
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_soft_deleted)
        self.assertFalse(self.user.is_active)

    def test_soft_delete_no_auth(self):
        url = "/api/delete-account/"
        data = {"confirm": True}
        response = self.client.delete(url, data)
        self.assertEqual(response.status_code, 401)

    def test_soft_delete_no_confirm(self):
        self.authenticate_with_cookies("user@test.de", "User123!")
        url = "/api/delete-account/"
        data = {}
        response = self.client.delete(url, data)
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_soft_deleted)

    # --- HARD DELETE (ADMIN) ---
    def test_hard_delete_success(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.deleted_user.pk).exists())

    def test_hard_delete_not_admin(self):
        self.authenticate_with_cookies("user@test.de", "User123!")
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 403)

    def test_hard_delete_no_auth(self):
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 401)

    def test_hard_delete_invalid_user(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = "/api/admin/delete-account/99999/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)

    # --- RESTORE ACCOUNT (ADMIN) ---
    def test_restore_account_success(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = f"/api/admin/restore-account/{self.deleted_user.pk}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.deleted_user.refresh_from_db()
        self.assertFalse(self.deleted_user.is_soft_deleted)
        self.assertTrue(self.deleted_user.is_active)

    def test_restore_account_not_admin(self):
        self.authenticate_with_cookies("user@test.de", "User123!")
        url = f"/api/admin/restore-account/{self.deleted_user.pk}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_restore_account_invalid_user(self):
        self.authenticate_with_cookies("admin@test.de", "Admin123!")
        url = f"/api/admin/restore-account/99999/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)


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
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_soft_deleted)

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
