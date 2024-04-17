from datetime import date
from django.db import models

class Video(models.Model):
    uploaded_at = models.DateField(default=date.today)
    title = models.CharField(max_length=150)
    description = models.CharField(max_length=500)
    video_file = models.FileField(upload_to='videos', blank=True, null=True)
    
    def __str__(self):
        return self.title
    

class VideoResolution(models.Model):
    original_video = models.ForeignKey(Video, related_name='resolutions', on_delete=models.CASCADE, null=True)
    resolution = models.CharField(max_length=20)
    converted_file = models.FileField(upload_to='videos', max_length=500)

    def __str__(self):
        return f"{self.original_video.title} - {self.resolution}"