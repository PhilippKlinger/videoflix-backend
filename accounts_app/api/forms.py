from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from accounts_app.models import CustomUser

class CustomUserCreationForm(UserCreationForm):
    """
    Form für die Benutzer-Erstellung im Django Admin.
    Hier kannst du zusätzliche Felder validieren oder das Layout anpassen.
    """
    class Meta:
        model = CustomUser
        fields = ("email", "username", "custom", "phone")

class CustomUserChangeForm(UserChangeForm):
    """
    Form für das Bearbeiten eines Users im Django Admin.
    """
    class Meta:
        model = CustomUser
        fields = ("email", "username", "custom", "phone", "is_active")
