from django.urls import path
from .views import RegisterUserView, LoginUserView, ActivateAccountView, RequestNewActivationLinkView, PasswordResetRequestView, PasswordResetConfirmView, DeleteAccountView, UserProfileView,  RegisterUserEmailView, VerifyResetCodeView


urlpatterns = [
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
]