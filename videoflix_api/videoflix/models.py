from datetime import date
from django.db import models
from users.models import Profile

class Video(models.Model):
    
    GENRE_CHOICES = [
        ('Action', 'Action'),
        ('Comedy', 'Comedy'),
        ('Crime', 'Crime'),
        ('Documentary', 'Documentary'),
        ('Mystery', 'Mystery'),
        ('Romance', 'Romance'),
        ('Sports', 'Sports')
    ]
    
    CATEGORY_CHOICES = [
        ('Movie', 'Movie'),
        ('TV-Show', 'TV-Show')
    ]
    
    
    uploaded_at = models.DateField(default=date.today)
    title = models.CharField(max_length=150)
    description = models.CharField(max_length=500)
    genre = models.CharField(max_length=100, choices=GENRE_CHOICES, default=GENRE_CHOICES[0][0])
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES, default=CATEGORY_CHOICES[0][0])
    video_file = models.FileField(upload_to='videos', blank=True, null=True)
    thumbnail = models.ImageField(upload_to='thumbnails/', blank=True, null=True)
    favorited_by = models.ManyToManyField(Profile, related_name='favorite_videos', blank=True)
    conversion_progress = models.IntegerField(default=0)
    current_resolution = models.CharField(max_length=10, blank=True, null=True)
    convert_job_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return self.title
    

class VideoResolution(models.Model):
    original_video = models.ForeignKey(Video, related_name='resolutions', on_delete=models.CASCADE, null=True)
    resolution = models.CharField(max_length=20)
    converted_file = models.FileField(upload_to='videos', max_length=500)

    def __str__(self):
        return f"{self.original_video.title} - {self.resolution}"