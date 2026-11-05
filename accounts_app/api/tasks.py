"""
Background tasks for account mails: the request only queues them, the mail worker sends them.
"""

import logging

import django_rq

from accounts_app.models import CustomUser
from .utils import send_activation_email, send_password_reset_email

logger = logging.getLogger(__name__)


def queue_activation_email(user):
    """
    Queue the activation mail for the mail worker.
    """
    _enqueue(deliver_activation_email, user)


def queue_password_reset_email(user):
    """
    Queue the password reset mail for the mail worker.
    """
    _enqueue(deliver_password_reset_email, user)


def deliver_activation_email(user_id):
    """
    Send the activation mail of an account.
    """
    _deliver(send_activation_email, user_id)


def deliver_password_reset_email(user_id):
    """
    Send the password reset mail of an account.
    """
    _deliver(send_password_reset_email, user_id)


def _enqueue(task, user):
    """
    Only the user id goes to Redis; address and token stay in the database.
    An outage must not fail the request: a new link can be requested later.
    """
    try:
        django_rq.get_queue("mail", autocommit=True).enqueue(task, user.pk)
    except Exception as e:
        logger.error("Failed to queue mail for user %s: %s", user.pk, type(e).__name__)


def _deliver(send_mail, user_id):
    """
    Load the account and send the mail. Logs without details: SMTP errors can contain the address.
    """
    try:
        send_mail(CustomUser.objects.get(pk=user_id))
    except CustomUser.DoesNotExist:
        logger.warning("Mail skipped, user %s no longer exists.", user_id)
    except Exception as e:
        logger.error("Failed to send mail to user %s: %s", user_id, type(e).__name__)
