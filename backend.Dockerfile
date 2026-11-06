# Pinned base: same tag in both stages, digest keeps the build reproducible.
ARG PYTHON_IMAGE=python:3.13.18-alpine3.24@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8

# --- build stage: install the Python packages into a virtualenv ---
FROM ${PYTHON_IMAGE} AS build

# The dev profile installs requirements-dev.txt with test and debug tools.
ARG REQUIREMENTS=requirements.txt

RUN apk add --no-cache gcc musl-dev postgresql-dev

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies before the source code, so the layer stays cached.
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r "/tmp/${REQUIREMENTS}"

# --- runtime stage: no compiler, no build tools ---
FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="videoflix-backend"
LABEL org.opencontainers.image.source="https://github.com/PhilippKlinger/videoflix-backend"

# ffmpeg/ffprobe: conversion (worker) and upload check (web); pg_isready: wait for the database
RUN apk add --no-cache ffmpeg postgresql-client && \
    addgroup -S app && adduser -S -G app -h /app app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .

# Static files are baked into the image (served by WhiteNoise). Settings require these two values
# to load; the placeholders only exist for this one command (no database access) and are not
# stored in the image. Media gets its owner before the volume is mounted.
RUN SECRET_KEY=collectstatic-build-only DB_PASSWORD=collectstatic-build-only \
    python manage.py collectstatic --noinput && \
    mkdir -p /app/media && \
    chown -R app:app /app/static /app/media && \
    chmod +x backend.entrypoint.sh

# Build information for /api/version/; last on purpose, so a new commit keeps the layers above cached.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA="${GIT_SHA}" BUILD_TIME="${BUILD_TIME}"

USER app

EXPOSE 8000

ENTRYPOINT ["./backend.entrypoint.sh"]
CMD ["web"]
