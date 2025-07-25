"""
API endpoints for accounts_app.
"""

from django.urls import path
from .views import (
    CustomLoginCookieView,
    CustomLoginCookieRefreshView,
    HardDeleteAccountView,
    LogoutView,
    RegisterUserView,
    ActivateAccountView,
    RequestNewActivationLinkView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    RestoreAccountView,
    SoftDeleteAccountView,
)

urlpatterns = [
    path('register/', RegisterUserView.as_view(), name='register-user'),
    path('login/', CustomLoginCookieView.as_view(), name='login-user'),
    path('token/refresh/', CustomLoginCookieRefreshView.as_view(), name='login-user-refresh'),
    path('activate/<str:activation_code>/', ActivateAccountView.as_view(), name='activate_account'),
    path('request-new-activation-link/', RequestNewActivationLinkView.as_view(), name='request_new_activation_link'),
    path('delete-account/', SoftDeleteAccountView.as_view(), name='soft-delete-account'),
    path('admin/delete-account/<int:pk>/', HardDeleteAccountView.as_view(), name='hard-delete-account'),
    path('admin/restore-account/<int:pk>/', RestoreAccountView.as_view(), name='restore-account'),
    path('logout/', LogoutView.as_view(), name='logout-user'),
    path('password_reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password_confirm/<str:uid>/<str:token>/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
