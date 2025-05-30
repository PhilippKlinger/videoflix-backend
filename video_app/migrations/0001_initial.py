# Generated by Django 5.2.1 on 2025-05-27 07:41

import datetime
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Video',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uploaded_at', models.DateField(default=datetime.date.today)),
                ('title', models.CharField(max_length=150)),
                ('description', models.CharField(max_length=500)),
                ('genre', models.CharField(choices=[('Action', 'Action'), ('Comedy', 'Comedy'), ('Crime', 'Crime'), ('Documentary', 'Documentary'), ('Mystery', 'Mystery'), ('Romance', 'Romance'), ('Sports', 'Sports')], default='Action', max_length=100)),
                ('category', models.CharField(choices=[('Movie', 'Movie'), ('TV-Show', 'TV-Show')], default='Movie', max_length=100)),
                ('video_file', models.FileField(blank=True, null=True, upload_to='videos')),
                ('thumbnail', models.ImageField(blank=True, null=True, upload_to='thumbnails/')),
                ('conversion_progress', models.IntegerField(default=0)),
                ('current_resolution', models.CharField(blank=True, max_length=10, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='VideoResolution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('resolution', models.CharField(max_length=20)),
                ('converted_file', models.FileField(max_length=500, upload_to='videos')),
                ('original_video', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resolutions', to='video_app.video')),
            ],
        ),
    ]
