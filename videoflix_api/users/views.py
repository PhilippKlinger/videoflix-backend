from django.shortcuts import render
from .serializers import PasswordResetRequestSerializer, PasswordResetSerializer, UserRegistrationSerializer, LoginSerializer
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework import views, status
from django.core.mail import send_mail
from django.urls import reverse
from django.conf import settings
from .models import CustomUser
from django.utils import timezone

class RegisterUserView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            user.is_active = False  # Benutzer zunächst als inaktiv markieren
            user.save()
            user.generate_activation_code()  # Generiere den Aktivierungscode
            activation_url = request.build_absolute_uri(reverse('activate_account', args=[user.activation_code]))
            send_mail(
                'Bestätigen Sie Ihr Konto',
                f'Bitte klicken Sie auf den Link, um Ihr Konto zu aktivieren: {activation_url}',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            return Response({"detail": "Bitte überprüfen Sie Ihre E-Mail, um Ihr Konto zu aktivieren."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ActivateAccountView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request, activation_code):
        try:
            user = CustomUser.objects.get(activation_code=activation_code, is_active=False)
            # Überprüfe, ob der Aktivierungscode noch gültig ist
            if timezone.now() > user.activation_code_expiry:
                return Response({"detail": "Der Aktivierungscode ist abgelaufen. Bitte fordern Sie einen neuen an."}, status=status.HTTP_400_BAD_REQUEST)
            user.is_active = True
            user.activation_code = None  # Lösche den Aktivierungscode
            user.activation_code_expiry = None  # Setze das Ablaufdatum zurück
            user.save()
            return Response({"detail": "Ihr Konto wurde erfolgreich aktiviert."}, status=status.HTTP_200_OK)
        except CustomUser.DoesNotExist:
            return Response({"detail": "Ungültiger Aktivierungscode. Bitte fordern Sie einen neuen an."}, status=status.HTTP_400_BAD_REQUEST)

class LoginUserView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = serializer.validated_data['user']
            if not user.is_active:
                 return Response({
                    "detail": "Konto nicht aktiviert. Bitte aktivieren Sie Ihr Konto oder fordern Sie einen neuen Aktivierungslink an.",
                    "resend_activation": True
                }, status=status.HTTP_401_UNAUTHORIZED)
            token, created = Token.objects.get_or_create(user=user)
            return Response({"token": token.key, "user_id": user.pk, "email": user.email}, status=status.HTTP_200_OK)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class RequestNewActivationLinkView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        try:
            user = CustomUser.objects.get(email=email, is_active=False)
            if not user.activation_code_expiry or timezone.now() > user.activation_code_expiry:
                user.generate_activation_code()  # Generiere einen neuen Code und setze das Ablaufdatum
                activation_url = request.build_absolute_uri(reverse('activate_account', args=[user.activation_code]))
                send_mail(
                    'Please activate your Account again',
                    f'Use this link to activate your account: {activation_url}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                return Response({"detail": "A new activation link has been sent. Please check your email account."}, status=status.HTTP_200_OK)
            else:
                return Response({"detail": "There is still an active and valid activation link available."}, status=status.HTTP_400_BAD_REQUEST)
        except CustomUser.DoesNotExist:
            return Response({"detail": "There is no inactive account linked to this email."}, status=status.HTTP_404_NOT_FOUND)

class PasswordResetRequestView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            user = CustomUser.objects.get(email=email)
            user.generate_activation_code()
            reset_url = request.build_absolute_uri(reverse('password_reset_confirm', args=[user.activation_code]))
            send_mail(
                'Passwort zurücksetzen',
                f'Bitte benutzen Sie diesen Link, um Ihr Passwort zurückzusetzen: {reset_url}',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            return Response({"detail": "Ein Link zum Zurücksetzen des Passworts wurde an Ihre E-Mail-Adresse gesendet."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class PasswordResetConfirmView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, activation_code):
        try:
            user = CustomUser.objects.get(activation_code=activation_code, is_active=True)
            serializer = PasswordResetSerializer(data=request.data)
            if serializer.is_valid():
                user.set_password(serializer.validated_data['new_password'])
                user.activation_code = None  # Lösche den Code nach Gebrauch
                user.save()
                return Response({"detail": "The password reset was successfully."}, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except CustomUser.DoesNotExist:
            return Response({"detail": "Invalid or expired reset link."}, status=status.HTTP_400_BAD_REQUEST)

class DeleteAccountView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        user = request.user

        # Optionale Sicherheitsüberprüfung: Bestätigung durch den Benutzer
        confirm = request.data.get('confirm', False)
        if not confirm:
            return Response({"detail": "Please confirm the cancellation of the account."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user.delete()
            return Response({"detail": "Account ahs been deleted successfully."}, status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
