#!/bin/sh
set -e

# One process per container: "web" serves the API, "worker" runs the RQ queue.
# Any other command (e.g. "python manage.py createsuperuser") is passed through.

wait_for_db() {
  echo "Waiting for PostgreSQL on $DB_HOST:$DB_PORT..."
  # -q for "quiet" (no output except errors)
  while ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; do
    sleep 1
  done
  echo "PostgreSQL is ready."
}

case "$1" in
  web)
    wait_for_db
    # Create migrations automatically in dev only (prod: migrations belong in the repo)
    if [ "${RUN_MAKEMIGRATIONS:-0}" = "1" ]; then
      python manage.py makemigrations
    fi
    python manage.py migrate --noinput
    # gthread: a few threads per worker, so slow uploads and HLS requests do not block each other.
    # No access log: activation and reset tokens are part of request paths.
    exec gunicorn core.wsgi:application \
      --bind 0.0.0.0:8000 \
      --worker-class gthread \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-4}" \
      --timeout "${GUNICORN_TIMEOUT:-120}"
    ;;
  worker)
    wait_for_db
    exec python manage.py rqworker default
    ;;
  *)
    exec "$@"
    ;;
esac
