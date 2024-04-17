import os
import subprocess
from django.core.files.storage import default_storage
from .models import VideoResolution

def convert_video(video_instance):
    input_path = video_instance.video_file.path
    resolutions = ['480p', '720p', '1080p']
    for res in resolutions:
        base, ext = os.path.splitext(video_instance.video_file.name)
        output_filename = f"{base}_{res}{ext}"
        output_path = os.path.join(default_storage.location, output_filename)
        output_path = os.path.normpath(output_path)
        
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f'scale=-2:{res.split("p")[0]}',             # Skalieren auf 480p
            '-c:v', 'libx264',                                  # Video-Codec: H.264
            '-preset', 'veryfast',                              # Schnelles Encoding
            '-c:a', 'copy',                                     # Audio kopieren ohne Neukodierung
            output_path
        ]
        try:
            subprocess.run(command, check=True)
            VideoResolution.objects.create(original_video=video_instance, resolution=res, converted_file=output_path)
            print(f"Video converted and saved to {output_path}")
        
        except subprocess.CalledProcessError as e:
            print(f"Failed to convert video: {e}")
