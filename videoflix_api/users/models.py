from django.db import models
from django.contrib.auth.models import AbstractUser
import uuid
from django.utils import timezone
from datetime import timedelta
from django.core.exceptions import ValidationError

class CustomUser(AbstractUser):
    custom = models.CharField(max_length=500, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)     
    is_active = models.BooleanField(default=True)
    activation_code = models.CharField(max_length=40, blank=True, null=True)
    activation_code_expiry = models.DateTimeField(blank=True, null=True)
    
    def generate_activation_code(self):
        self.activation_code = str(uuid.uuid4())
        self.activation_code_expiry = timezone.now() + timedelta(minutes=1)
        self.save()
        
    def save(self, *args, **kwargs):
        if self.pk and self.profiles.count() >= 5:
            raise ValidationError("Ein Benutzer darf nicht mehr als vier Profile haben.")
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.username


class Profile(models.Model):
    AVATAR_CHOICES = [
        ('avatars/avatar_0.png', 'Avatar 0'),
        ('avatars/avatar_1.png', 'Avatar 1'),
        ('avatars/avatar_2.png', 'Avatar 2'),
        ('avatars/avatar_3.png', 'Avatar 3'),
        ('avatars/avatar_4.png', 'Avatar 4'),
        ('avatars/avatar_5.png', 'Avatar 5'),
        ('avatars/avatar_6.png', 'Avatar 6'),
    ]    
    
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='profiles')
    name = models.CharField(max_length=100)
    avatar = models.CharField(max_length=255, choices=AVATAR_CHOICES)

    def __str__(self):
        return f"{self.name} ({self.user.username})"