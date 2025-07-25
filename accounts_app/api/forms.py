"""
Custom admin forms for the CustomUser model.
"""

from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from accounts_app.models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    """
    Form for creating new users in the admin.
    """

    class Meta:
        model = CustomUser
        fields = ("email", "username", "custom", "phone")


class CustomUserChangeForm(UserChangeForm):
    """
    Form for updating users in the admin.
    """

    class Meta:
        model = CustomUser
        fields = ("email", "username", "custom", "phone", "is_active")
