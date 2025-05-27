from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

class EmailBackend(ModelBackend):
    """
    Custom Authentication Backend, um Login per E-Mail und Passwort zu erlauben.
    Muss in den Django-Einstellungen unter AUTHENTICATION_BACKENDS eingetragen werden!
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username)
            if user.check_password(password):
                return user
        except UserModel.DoesNotExist:
            return None
