from django.contrib import admin
from .api.forms import CustomUserCreationForm, CustomUserChangeForm
from .models import CustomUser
from django.contrib.auth.admin import UserAdmin

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    fieldsets = (
        ('Custom Data', {'fields': ('custom', 'phone',)}),
        *UserAdmin.fieldsets, 
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
    