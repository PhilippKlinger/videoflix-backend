# Generated by Django 5.0.4 on 2024-04-17 16:14

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('videoflix', '0008_alter_video_title_alter_videoresolution_file'),
    ]

    operations = [
        migrations.RenameField(
            model_name='video',
            old_name='video_file',
            new_name='video_filepath',
        ),
        migrations.RenameField(
            model_name='videoresolution',
            old_name='file',
            new_name='converted_filepath',
        ),
        migrations.RenameField(
            model_name='videoresolution',
            old_name='video',
            new_name='original_video',
        ),
    ]
