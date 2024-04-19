from django.contrib import admin
from .forms import CustomUserCreationForm, CustomUserChangeForm
from .models import CustomUser
from django.contrib.auth.admin import UserAdmin

@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    fieldsets = (
        (
            'Individuelle Daten',
            {
                'fields': (
                    'custom',
                    'phone',
                    'address',
                )
            }
        ),
        *UserAdmin.fieldsets, 
    )