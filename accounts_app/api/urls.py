from django.urls import path
from .views import (
    HardDeleteAccountView,
    RegisterUserView,
    LoginUserView,
    ActivateAccountView,
    RequestNewActivationLinkView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    RestoreAccountView,
    SoftDeleteAccountView,
)

urlpatterns = [
#------------------------------------------authentication---------------------------------------------------#
    path('register/', RegisterUserView.as_view(), name='register-user'),
    path('login/', LoginUserView.as_view(), name='login-user'),
    path('activate/<str:activation_code>/', ActivateAccountView.as_view(), name='activate_account'),
    path('request-new-activation-link/', RequestNewActivationLinkView.as_view(), name='request_new_activation_link'),
    path('delete-account/', SoftDeleteAccountView.as_view(), name='soft-delete-account'),
    path('admin/delete-account/<int:pk>/', HardDeleteAccountView.as_view(), name='hard-delete-account'),
    path('admin/restore-account/<int:pk>/', RestoreAccountView.as_view(), name='restore-account'),
        
    #------------------------------------------password reset---------------------------------------------------#
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password-reset-confirm/<str:activation_code>/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]