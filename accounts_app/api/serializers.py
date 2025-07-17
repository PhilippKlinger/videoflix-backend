import uuid
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from accounts_app.models import CustomUser


class UserRegistrationSerializer(serializers.ModelSerializer):
    confirmed_password = serializers.CharField(write_only=True)

    class Meta:
        model = CustomUser
        fields = ("email", "password", "confirmed_password")
        extra_kwargs = {"password": {"write_only": True}}

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "Please check your entries and try again."
            )
        return value

    def validate(self, data):
        if data["password"] != data["confirmed_password"]:
            raise serializers.ValidationError("Passwords do not match.")
        validate_password(data["password"])
        return data

    def create(self, validated_data):
        validated_data.pop("confirmed_password", None)
        # User ist zunächst inaktiv (Aktivierung per Email)
        if "username" not in validated_data or not validated_data["username"]:
            validated_data["username"] = (
                validated_data["email"].split("@")[0] + uuid.uuid4().hex[:6]
            )

        user = CustomUser.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            username=validated_data["username"],
            is_active=False,
        )
        return user

class CustomLoginCookieSerializer(TokenObtainPairSerializer):
    username_field = "email"

    def validate(self, attrs):
        email = attrs.get(self.username_field)
        password = attrs.get("password")

        user = authenticate(
            request=self.context.get("request"),
            username=email,
            password=password,
        )
        
        if not user or not user.is_active or getattr(user, "is_soft_deleted", False):
            raise serializers.ValidationError(
                "Please check your entries and try again."
            )
            
        self.user = user
        data = super().validate(attrs)
        data["user"] = user  # <- diese Zeile ist entscheidend
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        return value


class PasswordResetSerializer(serializers.Serializer):
    new_password = serializers.CharField(required=True, write_only=True)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError("Passwords do not match.")
        validate_password(data["new_password"])
        return data
