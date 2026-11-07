"""
Views for authentication, account activation, password reset, and account management.
"""

import base64
import secrets
from rest_framework import views, status
from rest_framework.exceptions import UnsupportedMediaType
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone

from accounts_app.models import DEMO_LIFETIME, MAX_ACTIVE_DEMOS, CustomUser
from .serializers import (
    CustomLoginCookieSerializer,
    UserRegistrationSerializer,
    PasswordResetRequestSerializer,
    PasswordResetSerializer,
)
from .tasks import queue_activation_email, queue_password_reset_email
from .utils import (
    generate_activation_code,
    is_admin,
    set_jwt_cookie,
)
from django.middleware.csrf import get_token


class RegisterUserView(views.APIView):
    """
    API views for user registration.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_mail"

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            user.activation_code, user.activation_code_expiry = (
                generate_activation_code()
            )
            user.save()
            queue_activation_email(user)
            return Response(
                {"detail": "Please check your email to activate your account."},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ActivateAccountView(views.APIView):
    """
    API view for activating a user account via UID and activation token.
    """

    permission_classes = [AllowAny]

    def get(self, request, uid, token):
        try:
            uid_decoded = base64.urlsafe_b64decode(uid.encode()).decode()
            user = CustomUser.objects.get(
                pk=uid_decoded, activation_code=token, is_active=False
            )
            if (
                not user.activation_code_expiry
                or timezone.now() > user.activation_code_expiry
            ):
                return Response(
                    {"detail": "Activation link expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.is_active = True
            user.activation_code = None
            user.activation_code_expiry = None
            user.save()
            return Response(
                {"detail": "Account successfully activated."}, status=status.HTTP_200_OK
            )
        except CustomUser.DoesNotExist:
            return Response(
                {"detail": "Invalid activation link."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            return Response(
                {"detail": "Account activation failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class CustomLoginCookieView(views.APIView):
    """
    API view for user login using email and password, sets JWT tokens as cookies.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    # No authentication here: an old or expired cookie must not block a new login.
    authentication_classes = []
    # JSON only: a foreign page can submit a form cross-site, but not JSON (login CSRF).
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        serializer = CustomLoginCookieSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            refresh = RefreshToken.for_user(user)

            response = Response(
                {"detail": "login successfully"}, status=status.HTTP_200_OK
            )

            set_jwt_cookie(response, "access_token", str(refresh.access_token), 60 * 25)
            set_jwt_cookie(response, "refresh_token", str(refresh), 60 * 60 * 24 * 7)
            # Sets the csrftoken cookie the frontend sends back as X-CSRFToken.
            get_token(request)
            return response

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DemoStartView(views.APIView):
    """
    Start a demo account with one click, without mail (VFX-28, masterplan E11).

    The account has member rights, no usable password and expires after 2 hours.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "demo"

    def post(self, request, *args, **kwargs):
        # JSON only, also for an empty body: a foreign page could otherwise
        # log visitors into a demo account with a plain HTML form.
        if request.content_type.split(";")[0].strip() != "application/json":
            raise UnsupportedMediaType(request.content_type)
        if CustomUser.objects.active_demos().count() >= MAX_ACTIVE_DEMOS:
            return Response(
                {"detail": "All demo slots are in use. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        email = f"demo-{secrets.token_hex(8)}@demo.invalid"
        user = CustomUser.objects.create_user(
            username=email, email=email, password=None, is_active=True, is_demo=True
        )
        refresh = RefreshToken.for_user(user)
        response = Response({"detail": "demo started"}, status=status.HTTP_201_CREATED)
        lifetime = int(DEMO_LIFETIME.total_seconds())
        set_jwt_cookie(response, "access_token", str(refresh.access_token), min(60 * 25, lifetime))
        set_jwt_cookie(response, "refresh_token", str(refresh), lifetime)
        get_token(request)
        return response


class CustomLoginCookieRefreshView(views.APIView):
    """
    API view to refresh access token using a refresh token from cookies.
    """

    permission_classes = [AllowAny]
    # No authentication here: the access cookie is usually expired when this is called.
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get("refresh_token")

        if not refresh_token:
            return Response(
                {"detail": "No refresh token provided."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refresh = RefreshToken(refresh_token)
            # The token stays valid after deactivation, so the account is checked here (VFX-53).
            if not CustomUser.objects.filter(pk=refresh["user_id"], is_active=True).exists():
                return Response(
                    {"detail": "Invalid or expired refresh token."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            access_token = str(refresh.access_token)

            response = Response(
                {"message": "access token refreshed"}, status=status.HTTP_200_OK
            )

            set_jwt_cookie(response, "access_token", access_token, 60 * 25)

            return response

        except TokenError:
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            ) 


class RequestNewActivationLinkView(views.APIView):
    """
    API view to request a new activation link for inactive accounts.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_mail"

    def post(self, request):
        email = request.data.get("email")
        user = CustomUser.objects.find_by_email(email, is_active=False)
        if user and (
            not user.activation_code_expiry
            or timezone.now() > user.activation_code_expiry
        ):
            user.activation_code, user.activation_code_expiry = (
                generate_activation_code()
            )
            user.save()
            queue_activation_email(user)
        return Response(
            {
                "detail": "If an inactive account with this email exists, a new activation link has been sent."
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(views.APIView):
    """
    API view to request a password reset link.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_mail"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data["email"]
            user = CustomUser.objects.find_by_email(email, is_active=True)
            if user:
                user.activation_code, user.activation_code_expiry = (
                    generate_activation_code()
                )
                user.save()
                queue_password_reset_email(user)
            return Response(
                {
                    "detail": "If an account with this email exists, a reset link has been sent."
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmView(views.APIView):
    """
    API view to confirm password reset with UID and token.
    """

    permission_classes = [AllowAny]

    def post(self, request, uid, token):
        try:
            uid_decoded = base64.urlsafe_b64decode(uid.encode()).decode()
            user = CustomUser.objects.get(
                pk=uid_decoded, activation_code=token, is_active=True
            )
            if (
                not user.activation_code_expiry
                or timezone.now() > user.activation_code_expiry
            ):
                return Response(
                    {"detail": "Reset link expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = PasswordResetSerializer(data=request.data)
            if serializer.is_valid():
                user.set_password(serializer.validated_data["new_password"])
                user.activation_code = None
                user.activation_code_expiry = None
                user.save()
                return Response(
                    {"detail": "Password reset successful."}, status=status.HTTP_200_OK
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response(
                {"detail": "Invalid or expired reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class DeleteAccountView(views.APIView):
    """
    Immediately and permanently delete the authenticated user's account with all uploads and
    their files. This cannot be undone. Requires "confirm": true and clears the auth cookies.
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request):
        confirm = request.data.get("confirm", False)
        if not confirm:
            return Response(
                {"detail": "Please confirm account deletion."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # CASCADE removes the rows, the video pre_delete signal removes the files.
        request.user.delete()
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie("access_token", path="/", samesite="Strict")
        response.delete_cookie("refresh_token", path="/", samesite="Strict")
        return response


class HardDeleteAccountView(views.APIView):
    """
    API view for admin to permanently delete a user account.
    """

    permission_classes = [IsAdminUser]

    def delete(self, request, pk):
        try:
            user = CustomUser.objects.get(pk=pk)
            user.delete()
            return Response(
                {"detail": "Account has been permanently deleted."},
                status=status.HTTP_204_NO_CONTENT,
            )
        except CustomUser.DoesNotExist:
            return Response(
                {"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )


class LogoutView(views.APIView):
    """
    API view to log out a user by clearing JWT cookies.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        response = Response(
            {"detail": "Logged out successfully."}, status=status.HTTP_200_OK
        )
        response.delete_cookie("access_token", path="/", samesite="Strict")
        response.delete_cookie("refresh_token", path="/", samesite="Strict")
        return response


class MeView(views.APIView):
    """
    API view to return the logged-in account and its role.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "role": _role(user),
            },
            status=status.HTTP_200_OK,
        )


def _role(user):
    """
    Return the role of an account: admin, demo or member (masterplan G2).
    """
    if is_admin(user):
        return "admin"
    if user.is_demo:
        return "demo"
    return "member"
