# Videoflix Backend

This is the backend REST API for **Videoflix**, a video streaming platform inspired by Netflix, built with Django, Django REST Framework, and Docker.  
Videoflix allows users to register, upload videos, stream in multiple resolutions (HLS), and manage their accounts securely.

---

## Features

- **User Registration & Authentication**
  - Secure JWT-based login (cookies)
  - Email activation & password reset via email
- **Video Management**
  - Authenticated video upload with automatic processing (thumbnail + HLS transcoding)
  - Video streaming in multiple resolutions (HLS)
- **Admin Features**
  - Permanent account deletion
  - Admin interface with import/export for data
- **API Documentation**
  - Interactive Swagger & Redoc docs (auto-generated from code)
- **Modern Stack & Workflow**
  - Dockerized for easy setup
  - Redis for background tasks (video processing) and caching

---

## Tech Stack

- **Python 3.13**
- **Django 5**
- **Django REST Framework**
- **PostgreSQL**
- **Redis**
- **django-rq**
- **ffmpeg**
- **Swagger & Redoc**

---

## Getting Started

### Prerequisites

- [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) installed (recommended)
- No manual Python or Postgres setup needed

### Quickstart (Docker)

```bash
# 1. Clone the repository  
git clone git@github.com:PhilippKlinger/videoflix-backend.git 
cd videoflix-backend  

# 2. Make a copy of the .env.template
cp .env.dev.template .env -> local development
cp .env.prod.template .env -> production server

# 3. Enter your secrets, db settings etc. Be sure to set the right port for CORS.
http://127.0.0.1:5500/ -> Live Server
http://127.0.0.1:4200/ -> Angular

# 4. Build and start all services (backend, Postgres, Redis)  
docker compose --profile dev up -d --build -> local development
docker compose --profile prod up -d --build -> local check of the production image (production runs via the videoflix-deploy repository)

🟢 The backend, database, Redis, and worker will be set up automatically.  
🟢 Migrations run when `web` starts; static files are built into the image.  
🟢 `web` (Gunicorn), `worker` (RQ, ffmpeg) and `mail-worker` (RQ, account mails) run as separate containers from the same image.

# 5. Create an admin account once (no default accounts in the image)
docker compose --profile dev exec web python manage.py createsuperuser
```
---

## Local Development (advanced, optional)

If you really want to run outside Docker, adjust your environment and DB settings accordingly.

---

## API Overview

All endpoints are prefixed with `/api/`.

Example endpoints:

- Registration: `POST /api/register/`
- Login (JWT cookie): `POST /api/login/`
- Video upload: `POST /api/upload/`
- Video list: `GET /api/video/`
- Stream video (HLS): `GET /api/video/<video_id>/<resolution>/<filename>`
- Account actions: `/api/delete-account/`, `/api/password_reset/`, etc.

Full details: See Swagger or Redoc docs below.

---

## API Documentation (Swagger & Redoc)

After running `docker-compose up`, view docs at:
```bash
- **Swagger UI**
http://127.0.0.1:8000/swagger/
- **Redoc UI**
http://127.0.0.1:8000/redoc/
```
You can:

- See all endpoints, parameters, models, responses, and error codes.
- Try out requests (register, login, upload videos) directly in the browser.
- Test endpoints requiring authentication with the JWT cookies from `POST /api/login/`; the "Authorize" button only offers Basic auth, which this API does not accept.

---

## Development Tips
```bash
**Testing**  
Run tests inside the running container:

# Tests im Container (DEV-Profile):
docker compose --profile dev exec web sh -lc "
  python -m coverage run manage.py test && coverage report
"

# Logs:
docker compose logs -f web worker mail-worker            # dev
docker compose logs -f web-prod worker-prod mail-worker-prod  # prod

# Dependencies: requirements.txt (runtime, used by the prod image) and
# requirements-dev.txt (test and debug tools, used by the dev profile).
# Dependabot keeps the pins up to date; after a change rebuild the image:
docker compose --profile dev up -d --build
docker compose --profile dev exec web sh -lc "
  python manage.py check &&
  python manage.py makemigrations --check --dry-run &&
  python -m coverage run manage.py test && coverage report
"

# Production runs the published images via the videoflix-deploy repository.
# Local check of the production image:
docker compose --profile prod up -d --build

**Media & Static files**  
Uploaded videos are persisted in a Docker volume. Static files are built into the production image; only the dev profile keeps them in a volume.

**Environment variables**  
Sensitive settings are loaded from `.env` (see `.env.dev.template` and `.env.prod.template` for reference).

**Admin UI**  
Access Django Admin at [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/) (with the account from `createsuperuser`, see step 5 of the Quickstart).
```
---

## Example Workflow

1. Register a new account (`/api/register/`)
2. Activate via email link
3. Login to receive authentication cookies
4. Upload videos using `/api/upload/`
5. View and stream videos via the `/api/video/` endpoints

---