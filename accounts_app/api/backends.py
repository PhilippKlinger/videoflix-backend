"""
Custom authentication backend to allow login with email and password.
"""

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

class EmailBackend(ModelBackend):
    """
    Authenticate using email and password instead of username.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        # The JWT serializer authenticates a second time and passes the address as "email".
        email = username or kwargs.get(UserModel.USERNAME_FIELD)
        user = UserModel.objects.find_by_email(email)
        if user and user.check_password(password):
            return user
        return None
