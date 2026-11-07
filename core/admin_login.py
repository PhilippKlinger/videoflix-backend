"""
Rate limit for the Django admin login; the RQ dashboard redirects to the same page.
"""

from math import ceil

from django.contrib import admin
from django.http import HttpResponse
from rest_framework.throttling import SimpleRateThrottle


class AdminLoginThrottle(SimpleRateThrottle):
    """
    Same rate, Redis counter and client IP logic as POST /api/login/ (VFX-29).
    One shared counter, so the second login page gives no extra attempts.
    """

    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def throttled_admin_login(request, extra_context=None):
    """
    Answer 429 before the login form runs, so a blocked attempt costs no password hashing.
    """
    if request.method == "POST":
        throttle = AdminLoginThrottle()
        if not throttle.allow_request(request, None):
            return HttpResponse(
                "Too many login attempts. Please try again later.",
                status=429,
                content_type="text/plain",
                headers={"Retry-After": str(ceil(throttle.wait()))},
            )
    return admin.site.login(request, extra_context)
