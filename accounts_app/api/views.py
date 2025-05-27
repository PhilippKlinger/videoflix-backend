from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.authtoken.models import Token
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from accounts_app.models import CustomUser
from .serializers import (
    UserRegistrationSerializer, LoginSerializer, PasswordResetRequestSerializer, PasswordResetSerializer
)
from .utils import (
    send_activation_email, send_password_reset_email, generate_activation_code
)
from django.utils import timezone


class RegisterUserView(views.APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            user.activation_code, user.activation_code_expiry = generate_activation_code()
            user.save()
            send_activation_email(user, request)
            return Response({"detail": "Please check your email to activate your account."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ActivateAccountView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request, activation_code):
        try:
            user = CustomUser.objects.get(activation_code=activation_code, is_active=False)
            if timezone.now() > user.activation_code_expiry:
                return redirect(f'{settings.FRONTEND_URL}login?status=expired')
            user.is_active = True
            user.activation_code = None
            user.activation_code_expiry = None
            user.save()
            return redirect(f'{settings.FRONTEND_URL}login?status=activated')
        except CustomUser.DoesNotExist:
            return redirect(f'{settings.FRONTEND_URL}login?status=invalid')

class LoginUserView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = serializer.validated_data['user']
            token, created = Token.objects.get_or_create(user=user)
            return Response({"token": token.key, "user_id": user.pk, "email": user.email}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class RequestNewActivationLinkView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        try:
            user = CustomUser.objects.get(email=email, is_active=False)
            if not user.activation_code_expiry or timezone.now() > user.activation_code_expiry:
                user.activation_code, user.activation_code_expiry = generate_activation_code()
                user.save()
                send_activation_email(user, request)
        except CustomUser.DoesNotExist:
            pass
        return Response(
            {"detail": "If an inactive account with this email exists, a new activation link has been sent."},
            status=status.HTTP_200_OK
        )

class PasswordResetRequestView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            try:
                user = CustomUser.objects.get(email=email, is_active=True)
                user.activation_code, user.activation_code_expiry = generate_activation_code()
                user.save()
                send_password_reset_email(user, request)
            except CustomUser.DoesNotExist:
                # Immer gleiche Response zurückgeben!
                pass
            return Response({"detail": "If an account with this email exists, a reset link has been sent."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class PasswordResetConfirmView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, activation_code):
        try:
            user = CustomUser.objects.get(activation_code=activation_code, is_active=True)
            if timezone.now() > user.activation_code_expiry:
                return Response({"detail": "Reset link expired."}, status=status.HTTP_400_BAD_REQUEST)
            serializer = PasswordResetSerializer(data=request.data)
            if serializer.is_valid():
                user.set_password(serializer.validated_data['new_password'])
                user.activation_code = None
                user.activation_code_expiry = None
                user.save()
                return Response({"detail": "Password reset successful."}, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except CustomUser.DoesNotExist:
            return Response({"detail": "Invalid or expired reset link."}, status=status.HTTP_400_BAD_REQUEST)

class SoftDeleteAccountView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user
        confirm = request.data.get('confirm', False)
        if not confirm:
            return Response({"detail": "Please confirm account deletion."}, status=status.HTTP_400_BAD_REQUEST)
        user.is_active = False
        user.is_soft_deleted = True
        user.save()
        return Response({"detail": "Account has been deactivated (soft delete)."}, status=status.HTTP_204_NO_CONTENT)

class HardDeleteAccountView(views.APIView):
    permission_classes = [IsAdminUser]

    def delete(self, request, pk):
        try:
            user = CustomUser.objects.get(pk=pk)
            user.delete()
            return Response({"detail": "Account has been permanently deleted."}, status=status.HTTP_204_NO_CONTENT)
        except CustomUser.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        
class RestoreAccountView(views.APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            user = CustomUser.objects.get(pk=pk, is_soft_deleted=True)
            user.is_active = True
            user.is_soft_deleted = False
            user.save()
            return Response({"detail": "Account has been restored."}, status=status.HTTP_200_OK)
        except CustomUser.DoesNotExist:
            return Response({"detail": "User not found or not deleted."}, status=status.HTTP_404_NOT_FOUND)