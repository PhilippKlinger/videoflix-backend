# Generated by Django 5.0.4 on 2024-05-28 17:42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('videoflix', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='video',
            name='thumbnail',
            field=models.ImageField(blank=True, null=True, upload_to='thumbnails/'),
        ),
    ]