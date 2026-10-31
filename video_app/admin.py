from django.contrib import admin
from .models import Video, VideoProgress, VideoResolution
from import_export import resources
from import_export.admin import ImportExportModelAdmin


class VideoResolutionInline(admin.TabularInline):
    model = VideoResolution
    extra = 1


class VideoResource(resources.ModelResource):
    class Meta:
        model = Video


class VideoProgressResource(resources.ModelResource):
    class Meta:
        model = VideoProgress


class VideoProgressInline(admin.TabularInline):
    model = VideoProgress
    extra = 0
    readonly_fields = ("user", "progress_seconds", "updated_at")
    can_delete = False


@admin.register(Video)
class VideoAdmin(ImportExportModelAdmin):
    inlines = [VideoResolutionInline, VideoProgressInline]
    list_display = ("title", "description", "category", "created_at")
    search_fields = ("title", "description")


@admin.register(VideoResolution)
class VideoResolutionAdmin(admin.ModelAdmin):
    list_display = ["original_video", "resolution", "converted_file"]


@admin.register(VideoProgress)
class VideoProgressAdmin(ImportExportModelAdmin):
    list_display = ("user", "video", "progress_seconds", "updated_at")
    search_fields = ("user__email", "video__title")
    list_filter = ("updated_at",)
    resource_class = VideoProgressResource
