"""
URL configuration for the core Django project.

Routes all project URLs and integrates API documentation.
"""

import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from rest_framework.permissions import AllowAny
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from core.settings import BASE_DIR

schema_view = get_schema_view(
    openapi.Info(
        title="Videoflix API",
        default_version="v1",
        description="API documentation for the Videoflix backend.",
    ),
    public=True,
    permission_classes=[AllowAny],
    authentication_classes=[]
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include("accounts_app.api.urls")),
    path("api/", include("video_app.api.urls")),
    path("swagger/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=os.path.join(BASE_DIR, "static"))
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
        path("api-auth/", include("rest_framework.urls")),
    ] + urlpatterns
