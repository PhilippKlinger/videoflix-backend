import uuid
from datetime import timedelta

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# Demo accounts expire 2 hours after they were started (masterplan E11).
DEMO_LIFETIME = timedelta(hours=2)
MAX_ACTIVE_DEMOS = 20


class CustomUserManager(BaseUserManager):
    """Manager for CustomUser."""
    
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        """
        Create and save a user with the given email and password.
        """
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and save a superuser with the given email and password.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        if extra_fields.get("is_active") is not True:
            raise ValueError("Superuser must have is_active=True.")

        return self.create_user(email, password, **extra_fields)

    def get_by_natural_key(self, username):
        """
        Lookup of ModelBackend for the API and the admin login.
        """
        # Rollback leftover: rows of the old soft delete stay dead (see CustomUser.is_soft_deleted).
        return self.get(**{self.model.USERNAME_FIELD: username, "is_soft_deleted": False})

    def find_by_email(self, email, **filters):
        """
        Find an account whatever the case of the address, or return None.
        Older rows can differ only by case: the exact spelling wins, then the oldest row.
        Used for the login, a new activation link and the password reset.
        """
        # Rollback leftover: rows of the old soft delete stay dead (see CustomUser.is_soft_deleted).
        matches = self.filter(email__iexact=email, is_soft_deleted=False, **filters)
        return matches.filter(email=email).first() or matches.order_by("pk").first()

    def active_demos(self):
        """
        Demo accounts that have not expired yet.
        """
        return self.filter(is_demo=True, date_joined__gt=timezone.now() - DEMO_LIFETIME)

    def expired_demos(self):
        """
        Demo accounts past their lifetime, to be deleted by the cleanup command.
        """
        return self.filter(is_demo=True, date_joined__lte=timezone.now() - DEMO_LIFETIME)


class CustomUser(AbstractUser):
    """
    Custom user model for Videoflix authentication.

    Fields:
        custom: Optional custom data.
        phone: Optional phone number.
        email: Email address, unique, used for login.
        is_active: If the user is active.
        activation_code: Code for account activation and password reset.
        activation_code_expiry: Expiry datetime for the activation code.
        is_soft_deleted: Rollback leftover, see the comment on the field.
        is_demo: Demo account started without mail; expires after DEMO_LIFETIME.
    """
    custom = models.CharField(max_length=500, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=False)
    activation_code = models.CharField(max_length=40, blank=True, null=True)
    activation_code_expiry = models.DateTimeField(blank=True, null=True)
    # Only kept so an older image can still run after a rollback. Removal follows after 1.0.
    # Rows set to True by the old soft delete stay dead: every login, link, reset and token check skips them.
    is_soft_deleted = models.BooleanField(default=False)
    is_demo = models.BooleanField(default=False)
    
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def generate_activation_code(self):
        """
        Generate a new activation code and set expiry (default 15 minutes).
        """
        self.activation_code = str(uuid.uuid4())
        self.activation_code_expiry = timezone.now() + timedelta(minutes=15)

    @property
    def is_expired_demo(self):
        """
        True if this is a demo account past its lifetime.
        """
        return self.is_demo and self.date_joined <= timezone.now() - DEMO_LIFETIME

    def __str__(self):
        """
        String representation of the user.
        """
        return self.email