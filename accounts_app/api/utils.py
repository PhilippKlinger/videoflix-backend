import uuid
import os
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.urls import reverse
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def generate_activation_code():
    code = str(uuid.uuid4())
    expiry = timezone.now() + timedelta(hours=1)
    return code, expiry


def send_activation_email(user, request):
    activation_url = request.build_absolute_uri(reverse('activate_account', args=[user.activation_code]))
    subject = "Confirm your email"
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [user.email]
    context = {
        "user": user,
        "button_url": activation_url,
        "button_text": "Activate account",
        "title": "Confirm your email",
        "message": "To complete your registration and verify your email address, please click the link below:",
    }
    html_content = render_to_string("emails/base_email.html", context)
    msg = EmailMultiAlternatives(subject, "", from_email, to_email)
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def send_password_reset_email(user, request):
    reset_url = f"{settings.FRONTEND_URL}reset-password/{user.activation_code}/"
    subject = "Reset your Password"
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [user.email]
    context = {
        "user": user,
        "button_url": reset_url,
        "button_text": "Reset password",
        "title": "Reset your Password",
        "message": "Please click on the following link to reset your password:",
    }
    html_content = render_to_string("emails/base_email.html", context)
    msg = EmailMultiAlternatives(subject, "", from_email, to_email)
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def resend_activation_link(user, request):
    user.activation_code, user.activation_code_expiry = generate_activation_code()
    user.save()
    send_activation_email(user, request)
