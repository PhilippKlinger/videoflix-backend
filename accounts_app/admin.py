from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .api.forms import CustomUserCreationForm, CustomUserChangeForm
from .models import CustomUser

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """
    Admin config for CustomUser.
    """
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    fieldsets = (
        ('Custom Data', {'fields': ('custom', 'phone',)}),
        *UserAdmin.fieldsets,
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
