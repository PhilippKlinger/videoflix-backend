from django.contrib.auth.base_user import BaseUserManager
from django.db import models
from django.contrib.auth.models import AbstractUser
import uuid
from django.utils import timezone
from datetime import timedelta
from django.core.exceptions import ValidationError


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        if extra_fields.get("is_active") is not True:
            raise ValueError("Superuser must have is_active=True.")

        return self.create_user(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    custom = models.CharField(max_length=500, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=False)
    activation_code = models.CharField(max_length=40, blank=True, null=True)
    activation_code_expiry = models.DateTimeField(blank=True, null=True)
    is_soft_deleted = models.BooleanField(default=False)
    objects = CustomUserManager()

    def generate_activation_code(self):
        self.activation_code = str(uuid.uuid4())
        self.activation_code_expiry = timezone.now() + timedelta(
            minutes=1
        )  # Zeit für prod hochsetzen

    def __str__(self):
        return self.username
