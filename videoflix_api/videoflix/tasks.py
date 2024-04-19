import subprocess
from django.core.files.storage import default_storage
from .models import VideoResolution

def convert_video(video_instance):
    input_file = video_instance.video_file
    resolutions = ['480p', '720p', '1080p']
    
    for res in resolutions:
        base_name = input_file.name.rsplit('.', 1)[0]
        extension = input_file.name.split('.')[-1]
        output_filename = f"{base_name}_{res}.{extension}"
        output_path = f"{output_filename}"
        
        command = [
            'ffmpeg',
            '-i', input_file.path,
            '-vf', f'scale=-2:{res.split("p")[0]}',             # Skalieren auf 480p
            '-c:v', 'libx264',                                  # Video-Codec: H.264
            '-preset', 'veryfast',                              # Schnelles Encoding
            '-c:a', 'copy',                                     # Audio kopieren ohne Neukodierung
            default_storage.path(output_path)
        ]
        try:
            subprocess.run(command, check=True)
            VideoResolution.objects.create(original_video=video_instance, resolution=res, converted_file=output_path)
            print(f"Video converted and saved to {output_path}")
        
        except subprocess.CalledProcessError as e:
            print(f"Failed to convert video: {e}")
