"""
Health check for Docker and the edge proxy (VFX-31), public build information (VFX-50).
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from rest_framework import status, views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class HealthView(views.APIView):
    """
    Report whether the database and the Redis cache answer.
    Errors are logged; the response only says "error" (no technical details).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        checks = {"database": _check_database(), "cache": _check_cache()}
        healthy = all(result == "ok" for result in checks.values())
        body = {"status": "ok" if healthy else "error", **checks}
        code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(body, status=code)


class VersionView(views.APIView):
    """
    Report which commit is running and when its image was built. Nothing else is public here.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        return Response({"commit": settings.GIT_SHA, "build_time": settings.BUILD_TIME})


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return "ok"
    except Exception:
        logger.exception("Health check: database not reachable")
        return "error"


def _check_cache():
    try:
        cache.set("health-check", "ok", timeout=10)
        if cache.get("health-check") != "ok":
            raise RuntimeError("cache returned a wrong value")
        return "ok"
    except Exception:
        logger.exception("Health check: cache not reachable")
        return "error"
