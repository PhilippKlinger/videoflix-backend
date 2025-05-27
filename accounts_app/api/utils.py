import uuid
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.urls import reverse
from django.conf import settings

def generate_activation_code():
    code = str(uuid.uuid4())
    expiry = timezone.now() + timedelta(hours=1)  
    return code, expiry

def send_activation_email(user, request):
    activation_url = request.build_absolute_uri(reverse('activate_account', args=[user.activation_code]))
    send_mail(
        'Activate your Videoflix account',
        f'Please click the link to activate your account: {activation_url}',
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )

def send_password_reset_email(user, request):
    reset_url = request.build_absolute_uri(reverse('password_reset_confirm', args=[user.activation_code]))
    send_mail(
        'Reset your Videoflix password',
        f'Use this link to reset your password: {reset_url}',
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


def resend_activation_link(user, request):
    user.activation_code, user.activation_code_expiry = generate_activation_code()
    user.save()
    send_activation_email(user, request)