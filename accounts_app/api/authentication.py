"""
Custom JWT authentication that reads tokens from cookies.
"""

from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework_simplejwt.authentication import JWTAuthentication

class JWTAuthenticationFromCookie(JWTAuthentication):
    """
    JWT authentication class that retrieves the access token from cookies if not present in headers.
    Cookie requests must pass the CSRF check, because browsers send cookies automatically (VFX-07).
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            return super().authenticate(request)

        raw_token = request.COOKIES.get("access_token")
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        self.enforce_csrf(request)
        return self.get_user(validated_token), validated_token

    def enforce_csrf(self, request):
        """
        Reject unsafe requests without a valid CSRF token, like DRF's SessionAuthentication.
        """
        def dummy_get_response(request):
            return None

        check = CSRFCheck(dummy_get_response)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")
