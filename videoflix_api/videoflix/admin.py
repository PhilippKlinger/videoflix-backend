from django.contrib import admin
from .models import Video, VideoResolution

class VideoResolutionInline(admin.TabularInline):
    model = VideoResolution
    extra = 1

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    inlines = [VideoResolutionInline]

@admin.register(VideoResolution)
class VideoResolutionAdmin(admin.ModelAdmin):
    list_display = ['original_video', 'resolution', 'converted_file']
