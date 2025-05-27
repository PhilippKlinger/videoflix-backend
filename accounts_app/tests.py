from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from accounts_app.models import CustomUser
from rest_framework.authtoken.models import Token
from django.utils import timezone

class AccountsIntegrationTests(APITestCase):
    def setUp(self):
        # Create admin
        self.admin = CustomUser.objects.create_user(
            username="admin", email="admin@test.de", password="Admin123!", is_staff=True, is_active=True
        )
        self.admin_token = Token.objects.create(user=self.admin)

        # Create regular user (activated)
        self.user = CustomUser.objects.create_user(
            username="testuser", email="user@test.de", password="User123!", is_active=True
        )
        self.user_token = Token.objects.create(user=self.user)

        # Create inactive user (not activated yet)
        self.inactive_user = CustomUser.objects.create_user(
            username="inactive", email="inactive@test.de", password="Inactive123!", is_active=False
        )

        # Create soft-deleted user
        self.deleted_user = CustomUser.objects.create_user(
            username="deleted", email="deleted@test.de", password="Deleted123!", is_active=False, is_soft_deleted=True
        )

   # --- REGISTRATION ---
    def test_registration_success(self):
        url = "/api/register/"
        data = {
            "email": "neu@example.com",
            "username": "neuuser",
            "password": "NeuUser123!",
            "password_confirm": "NeuUser123!"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(CustomUser.objects.filter(username="neuuser").exists())

    def test_registration_passwords_do_not_match(self):
        url = "/api/register/"
        data = {
            "email": "fail@example.com",
            "username": "failuser",
            "password": "pw1",
            "password_confirm": "pw2"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Passwords do not match.", str(response.data))

    def test_registration_duplicate_email(self):
        url = "/api/register/"
        data = {
            "email": "user@test.de",  # exists already
            "username": "anotheruser",
            "password": "Password123!",
            "password_confirm": "Password123!"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", str(response.data).lower())

    # --- LOGIN ---
    def test_login_success(self):
        url = "/api/login/"
        data = {
            "email": "user@test.de",
            "password": "User123!"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)

    def test_login_wrong_password(self):
        url = "/api/login/"
        data = {
            "email": "user@test.de",
            "password": "falsch"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    def test_login_missing_fields(self):
        url = "/api/login/"
        data = {"email": "user@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    def test_login_inactive_user(self):
        url = "/api/login/"
        data = {
            "email": "inactive@test.de",
            "password": "Inactive123!"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    def test_login_soft_deleted_user(self):
        url = "/api/login/"
        data = {
            "email": "deleted@test.de",
            "password": "Deleted123!"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    # --- ACTIVATION ---
    def test_activation_success(self):
        user = CustomUser.objects.create_user(
            username="activateuser", email="activate@test.de", password="Pw123456!", is_active=False
        )
        user.activation_code = "testcode"
        user.activation_code_expiry = timezone.now() + timezone.timedelta(hours=1)
        user.save()
        url = f"/api/activate/{user.activation_code}/"
        response = self.client.get(url)
       
        self.assertIn(response.status_code, [302, 301])
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNone(user.activation_code)

    def test_activation_expired(self):
        user = CustomUser.objects.create_user(
            username="expireduser", email="expired@test.de", password="Pw123456!", is_active=False
        )
        user.activation_code = "expiredcode"
        user.activation_code_expiry = timezone.now() - timezone.timedelta(hours=1)
        user.save()
        url = f"/api/activate/{user.activation_code}/"
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_activation_invalid_code(self):
        url = "/api/activate/thiscodedoesnotexist/"
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])

    # --- REQUEST NEW ACTIVATION LINK ---
    def test_request_new_activation_link_success(self):
        user = CustomUser.objects.create_user(
            username="newactivation", email="newactivation@test.de", password="pw", is_active=False
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
        url = "/api/password-reset/"
        data = {"email": "user@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)

    def test_password_reset_request_nonexistent(self):
        url = "/api/password-reset/"
        data = {"email": "idontexist@test.de"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)

    # --- PASSWORD RESET CONFIRM ---
    def test_password_reset_confirm_success(self):
        user = CustomUser.objects.create_user(
            username="pwreset", email="pwreset@test.de", password="OldPw123!", is_active=True
        )
        user.activation_code = "resetcode"
        user.activation_code_expiry = timezone.now() + timezone.timedelta(hours=1)
        user.save()
        url = f"/api/password-reset-confirm/{user.activation_code}/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPw123!"))
        self.assertIsNone(user.activation_code)

    def test_password_reset_confirm_expired(self):
        user = CustomUser.objects.create_user(
            username="pwexpired", email="pwexpired@test.de", password="OldPw123!", is_active=True
        )
        user.activation_code = "expiredpwcode"
        user.activation_code_expiry = timezone.now() - timezone.timedelta(hours=1)
        user.save()
        url = f"/api/password-reset-confirm/{user.activation_code}/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    def test_password_reset_confirm_invalid_code(self):
        url = "/api/password-reset-confirm/doesnotexist/"
        data = {"new_password": "NewPw123!", "confirm_password": "NewPw123!"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)

    # --- SOFT DELETE ---
    def test_soft_delete_success(self):
        url = "/api/delete-account/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.user_token.key)
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
        url = "/api/delete-account/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.user_token.key)
        data = {}
        response = self.client.delete(url, data)
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_soft_deleted)

    # --- HARD DELETE (ADMIN) ---
    def test_hard_delete_success(self):
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.admin_token.key)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.deleted_user.pk).exists())

    def test_hard_delete_not_admin(self):
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.user_token.key)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 403)

    def test_hard_delete_no_auth(self):
        url = f"/api/admin/delete-account/{self.deleted_user.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 401)

    def test_hard_delete_invalid_user(self):
        url = "/api/admin/delete-account/99999/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.admin_token.key)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)

    # --- RESTORE ACCOUNT (ADMIN) ---
    def test_restore_account_success(self):
        url = f"/api/admin/restore-account/{self.deleted_user.pk}/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.admin_token.key)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.deleted_user.refresh_from_db()
        self.assertFalse(self.deleted_user.is_soft_deleted)
        self.assertTrue(self.deleted_user.is_active)

    def test_restore_account_not_admin(self):
        url = f"/api/admin/restore-account/{self.deleted_user.pk}/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.user_token.key)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_restore_account_invalid_user(self):
        url = f"/api/admin/restore-account/99999/"
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.admin_token.key)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)