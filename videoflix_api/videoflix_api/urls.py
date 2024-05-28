"""
URL configuration for videoflix_api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from videoflix.views import VideoUploadView, VideoListView, VideoDetailView
from users.views import RegisterUserView, LoginUserView, ActivateAccountView, RequestNewActivationLinkView, PasswordResetRequestView, PasswordResetConfirmView, DeleteAccountView, UserProfileView,  RegisterUserEmailView, VerifyResetCodeView


urlpatterns = [
    path('admin/', admin.site.urls),
    path('upload/', VideoUploadView.as_view(), name='video-upload'),
    path('videos/', VideoListView.as_view(), name='video-list'),
    path('videos/<int:pk>/', VideoDetailView.as_view(), name='video-list-detail'),
    path('django-rq/', include('django_rq.urls')),
    #------------------------------------------authentication---------------------------------------------------#
    path('register-email/',  RegisterUserEmailView.as_view(), name='register-user-email'),
    path('register/', RegisterUserView.as_view(), name='register-user'),
    path('login/', LoginUserView.as_view(), name='login-user'),
    path('activate/<str:activation_code>/', ActivateAccountView.as_view(), name='activate_account'),
    path('request-new-activation-link/', RequestNewActivationLinkView.as_view(), name='request_new_activation_link'),
    path('delete-account/', DeleteAccountView.as_view(), name='delete-account'),
    #-------------------------------------user-profiles---------------------------------------------------------#
    path('profiles/', UserProfileView.as_view(), name='user-profiles'),
    path('profiles/<int:pk>/', UserProfileView.as_view(), name='user-profile-detail'),
    #------------------------------------------password reset---------------------------------------------------#
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('verify-reset-code/<str:activation_code>/', VerifyResetCodeView.as_view(), name='verify_reset_code'),
    path('password-reset-confirm/<str:activation_code>/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),


] + static(settings.MEDIA_URL, document_root = settings.MEDIA_ROOT)


# Debug auf True setzen in settings.py
if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),        
    ] + urlpatterns