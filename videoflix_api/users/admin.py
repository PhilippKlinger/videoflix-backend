from django.contrib import admin
from .forms import CustomUserCreationForm, CustomUserChangeForm
from .models import CustomUser, Profile
from django.contrib.auth.admin import UserAdmin

class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = True
    verbose_name_plural = 'profiles'
    fk_name = 'user'
    
@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    inlines = (ProfileInline, )
    fieldsets = (
        ('Custom Data', {'fields': ('custom', 'phone',)}),
        *UserAdmin.fieldsets, 
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')
    
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'avatar', 'user')
    list_filter = ('user__username',)
    search_fields = ('name', 'user__username')