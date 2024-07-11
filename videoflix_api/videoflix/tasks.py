import subprocess
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import VideoResolution, Video
import redis

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

def convert_video(video_instance, res, job_id):
    input_file = video_instance.video_file
    base_name = input_file.name.rsplit('.', 1)[0]
    extension = input_file.name.split('.')[-1]
    output_filename = f"{base_name}_{res}.{extension}"
    output_path = default_storage.path(output_filename)
    
    command = [
        'ffmpeg',
        '-i', default_storage.path(input_file.name),
        '-vf', f'scale=-2:{res.split("p")[0]}',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '32',
        '-c:a', 'copy',
        '-threads', '0',
        output_path
    ]

    redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, password=settings.REDIS_PASSWORD)

    try:
        redis_client.set(f'job:{job_id}:progress', 0)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        for line in process.stderr:
            if b'frame=' in line:
                progress = int(line.split(b'frame=')[1].strip().split(b' ')[0])  # Adjust this parsing according to ffmpeg output
                redis_client.set(f'job:{job_id}:progress', progress)
        
        process.wait()
        redis_client.set(f'job:{job_id}:progress', 100)

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command)

        VideoResolution.objects.create(original_video=video_instance, resolution=res, converted_file=output_filename)
        video_instance.current_resolution = res
        video_instance.save()
        print(f"Video converted and saved to {output_path}")
        
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert video: {e}")
        video_instance.current_resolution = None
        video_instance.save()
        redis_client.set(f'job:{job_id}:progress', 0)

# CRF-Werte und ihre Bedeutung
# 0: Lossless (verlustfrei) – höchste Qualität, aber die größte Dateigröße.
# 17-18: Visuell verlustfrei oder nahezu verlustfrei – hohe Qualität.
# 23: Standardwert – gute Qualität bei vernünftiger Dateigröße.
# 28: Niedrige Qualität – kleinere Dateigröße, aber sichtbare Qualitätsverluste.