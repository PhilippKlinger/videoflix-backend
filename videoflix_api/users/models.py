from django.db import models
from django.contrib.auth.models import AbstractUser
import uuid
from django.utils import timezone
from datetime import timedelta

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
    
    def __str__(self):
        return self.username
    