"""
Utility functions for account management and email sending.
"""

import base64
import uuid
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def generate_activation_code():
    """
    Generate a new activation code and expiry datetime (default 1 hour).
    """
    code = str(uuid.uuid4())
    expiry = timezone.now() + timedelta(hours=1)
    return code, expiry


def send_activation_email(user, request):
    """
    Send an activation email to the user.
    """
    uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
    token = user.activation_code
    activation_url = f"{settings.FRONTEND_URL}/pages/auth/activate.html?uid={uid}&token={token}"
    subject = "Confirm your email"
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [user.email]
    context = {
        "user": user,
        "button_url": activation_url,
        "button_text": "Activate account",
        "title": "Confirm your email",
        "message": "To complete your registration and verify your email address, please click the link below:",
        "advice": "If you did not create an account with us, please disregard this email",
    }
    html_content = render_to_string("emails/base_email.html", context)
    msg = EmailMultiAlternatives(subject, "", from_email, to_email)
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def send_password_reset_email(user, request):
    """
    Send a password reset email to the user.
    """
    uid = base64.urlsafe_b64encode(str(user.pk).encode()).decode()
    token = user.activation_code
    reset_url = f"{settings.FRONTEND_URL}/pages/auth/confirm_password.html?uid={uid}&token={token}"
    subject = "Reset your Password"
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [user.email]
    context = {
        "user": user,
        "button_url": reset_url,
        "button_text": "Reset password",
        "title": "Reset your Password",
        "message": "Please click on the following link to reset your password:",
        "hint": "Please note that for security reasons, this link is only valid for 1 hour.",
        "advice": "If you did not request a password reset, please ignore this email",
    }
    html_content = render_to_string("emails/base_email.html", context)
    msg = EmailMultiAlternatives(subject, "", from_email, to_email)
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def resend_activation_link(user, request):
    """
    Generate and send a new activation link to the user.
    """
    user.activation_code, user.activation_code_expiry = generate_activation_code()
    user.save()
    send_activation_email(user, request)


def encode_uid(user):
    """
    Encode user ID as a base64 string for use in URLs.
    """
    return base64.urlsafe_b64encode(str(user.pk).encode()).decode()
