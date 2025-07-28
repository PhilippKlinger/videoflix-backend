# Videoflix Backend

This is the backend REST API for **Videoflix**, a video streaming platform inspired by Netflix, built with Django, Django REST Framework, and Docker.  
Videoflix allows users to register, upload videos, stream in multiple resolutions (HLS), track watch progress, and manage their accounts securely.

---

## Features

- **User Registration & Authentication**
  - Secure JWT-based login (cookies)
  - Email activation & password reset via email
- **Video Management**
  - Authenticated video upload with automatic processing (thumbnail + HLS transcoding)
  - Video streaming in multiple resolutions (HLS)
  - Video progress tracking per user
- **Admin Features**
  - Soft and hard account deletion, account restoration
  - Admin interface with import/export for data
- **API Documentation**
  - Interactive Swagger & Redoc docs (auto-generated from code)
- **Modern Stack & Workflow**
  - Dockerized for easy setup
  - Redis for background tasks (video processing) and caching

---

## Tech Stack

- **Python 3.12+**
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
git clone git@github.com:PhilippKlinger/videoflix_backend.git  
cd videoflix_backend  

# 2. Make a copy of the .env.template
cp .env.template .env

# 3. Enter your secrets, db settings etc. Be sure to set the right port for CORS.
http://localhost:5500/ -> Live Server
http://localhost:4200/ -> Angular

# 4. Build and start all services (backend, Postgres, Redis)  
docker-compose up --build  

🟢 The backend, database, Redis, and worker will be set up automatically.  
🟢 All migrations, static/media setup, and superuser creation are handled by the entrypoint script.
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
http://localhost:8000/swagger/
- **Redoc UI**
http://localhost:8000/redoc/
```
You can:

- See all endpoints, parameters, models, responses, and error codes.
- Try out requests (register, login, upload videos) directly in the browser.
- Use the "Authorize" button to test endpoints requiring authentication (after logging in).

---

## Development Tips
```bash
**Testing**  
Run tests inside the running container:

docker-compose exec web sh  
python -m coverage run manage.py test  
coverage report

**Media & Static files**  
Uploaded videos and static files are persisted in Docker volumes.

**Environment variables**  
Sensitive settings are loaded from `.env` (see `.env.example` for reference).

**Admin UI**  
Access Django Admin at [http://localhost:8000/admin/](http://localhost:8000/admin/) (credentials set in `.env`).
```
---

## Example Workflow

1. Register a new account (`/api/register/`)
2. Activate via email link
3. Login to receive authentication cookies
4. Upload videos using `/api/upload/`
5. View and stream videos via the `/api/video/` endpoints

---