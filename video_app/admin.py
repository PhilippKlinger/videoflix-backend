from django.contrib import admin
from .models import Video, VideoResolution
from import_export import resources
from import_export.admin import ImportExportModelAdmin

class VideoResolutionInline(admin.TabularInline):
    model = VideoResolution
    extra = 1

@admin.register(Video)
class VideoAdmin(ImportExportModelAdmin):
    inlines = [VideoResolutionInline]
    list_display = ('title', 'description', 'uploaded_at')
    search_fields = ('title', 'description')

@admin.register(VideoResolution)
class VideoResolutionAdmin(admin.ModelAdmin):
    list_display = ['original_video', 'resolution', 'converted_file']

class VideoResource(resources.ModelResource):
    class Meta:
        model = Video
        