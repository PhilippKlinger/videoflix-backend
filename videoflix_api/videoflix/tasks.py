import subprocess
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import VideoResolution, Video

def create_thumbnail(video_instance):
    input_file = video_instance.video_file

    if not input_file:
        print("No file attached to the video instance.")
        return

    # Hier wird der Dateiname als String extrahiert
    file_name = input_file.name

    # Sicherstellen, dass der Dateiname vorhanden und korrekt ist
    if '.' in file_name:
        base_name = file_name.rsplit('.', 1)[0]
    else:
        print("Invalid file name. No extension found.")
        return

    output_filename = f"{base_name}_thumbnail.jpg"
    output_path = default_storage.path(output_filename)

    command = [
        'ffmpeg',
        '-i', default_storage.path(file_name),
        '-ss', '00:00:10',  # Capture the image 10 seconds into the video
        '-vframes', '1',
        '-q:v', '2',
        '-s', '640x480',  # Thumbnail size
        output_path
    ]

    try:
        subprocess.run(command, check=True) #shell=True
        with open(output_path, 'rb') as f:
            video_instance.thumbnail.save(output_filename, ContentFile(f.read()), save=True)
        print(f"Thumbnail created and saved to {output_filename}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to create thumbnail: {e}")
    finally:
        # Bereinige die temporär erstellte Datei
        default_storage.delete(output_filename)

def convert_video(video_instance):
    input_file = video_instance.video_file
    resolutions = ['480p', '720p', '1080p']
    total_resolutions = len(resolutions)
    
    for index, res in resolutions:
        base_name = input_file.name.rsplit('.', 1)[0]
        extension = input_file.name.split('.')[-1]
        output_filename = f"{base_name}_{res}.{extension}"
        output_path = default_storage.path(output_filename)
        
        command = [
            'ffmpeg',
            '-i', default_storage.path(input_file.name),
            '-vf', f'scale=-2:{res.split("p")[0]}',             # Skalieren auf 480p bpsw.
            '-c:v', 'libx264',                                  # Video-Codec: H.264
            '-preset', 'ultrafast',                              # Schnelles Encoding
            '-crf', '28',
            '-c:a', 'copy',                                     # Audio kopieren ohne Neukodierung
            output_path
        ]
        try:
            subprocess.run(command, check=True)
            VideoResolution.objects.create(original_video=video_instance, resolution=res, converted_file=output_filename)
            video_instance.conversion_progress = int(((index + 1) / total_resolutions) * 100)
            video_instance.current_resolution = res
            video_instance.save()
            print(f"Video converted and saved to {output_path}")
        
        except subprocess.CalledProcessError as e:
            print(f"Failed to convert video: {e}")
            video_instance.conversion_progress = 0
            video_instance.current_resolution = None
            video_instance.save()


# CRF-Werte und ihre Bedeutung
# 0: Lossless (verlustfrei) – höchste Qualität, aber die größte Dateigröße.
# 17-18: Visuell verlustfrei oder nahezu verlustfrei – hohe Qualität.
# 23: Standardwert – gute Qualität bei vernünftiger Dateigröße.
# 28: Niedrige Qualität – kleinere Dateigröße, aber sichtbare Qualitätsverluste.