from rest_framework_simplejwt.authentication import JWTAuthentication

class JWTAuthenticationFromCookie(JWTAuthentication):
   def authenticate(self, request):
        # Wenn bereits ein Authorization-Header da ist, nimm den Standardweg
        header = self.get_header(request)
        if header is not None:
            return super().authenticate(request)

        # Fallback: Access Token aus Cookie lesen
        raw_token = request.COOKIES.get("access_token")
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token