#!/bin/sh
set -e

echo "Warte auf PostgreSQL auf $DB_HOST:$DB_PORT..."

# -q for "quiet" (no output except errors)
# Loop while pg_isready is *not* successful (exit code != 0)
while ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; do
  echo "PostgreSQL ist nicht erreichbar - schlafe 1 Sekunde"
  sleep 1
done
echo "PostgreSQL ist bereit - fahre fort..."

python manage.py collectstatic --noinput

# Create migrations automatically in dev only (prod: migrations belong in the repo)
if [ "${RUN_MAKEMIGRATIONS:-0}" = "1" ]; then
  python manage.py makemigrations
fi
python manage.py migrate --noinput

# Optional superuser on the first deploy only
if [ "${CREATE_SUPERUSER:-1}" = "1" ] && [ -z "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "DJANGO_SUPERUSER_PASSWORD not set, skipping superuser creation."
elif [ "${CREATE_SUPERUSER:-1}" = "1" ]; then
python manage.py shell <<EOF
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
password = os.environ['DJANGO_SUPERUSER_PASSWORD']

if not User.objects.filter(username=username).exists():
    print(f"Creating superuser '{username}'...")
    # Pass the username here
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"Superuser '{username}' created.")
else:
    print(f"Superuser '{username}' already exists.")
EOF
fi

python manage.py rqworker default &

exec gunicorn core.wsgi:application --bind 0.0.0.0:8000
