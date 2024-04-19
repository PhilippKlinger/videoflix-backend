# Generated by Django 5.0.4 on 2024-04-19 09:31

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
                ('video_file', models.FileField(blank=True, null=True, upload_to='videos')),
            ],
        ),
        migrations.CreateModel(
            name='VideoResolution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('resolution', models.CharField(max_length=20)),
                ('converted_file', models.FileField(max_length=500, upload_to='videos')),
                ('original_video', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='resolutions', to='videoflix.video')),
            ],
        ),
    ]
