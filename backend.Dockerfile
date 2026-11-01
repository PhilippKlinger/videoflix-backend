FROM python:3.13-alpine

LABEL maintainer="hello@philipp-klinger.com"
LABEL version="1.0"
LABEL description="Python 3.13 Alpine"

# The dev profile installs requirements-dev.txt with test and debug tools.
ARG REQUIREMENTS=requirements.txt

WORKDIR /app

COPY . .

RUN apk update && \
    apk add --no-cache --upgrade bash && \
    apk add --no-cache postgresql-client ffmpeg && \
    apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r "${REQUIREMENTS}" && \
    apk del .build-deps && \
    chmod +x backend.entrypoint.sh

EXPOSE 8000

ENTRYPOINT [ "./backend.entrypoint.sh" ]
