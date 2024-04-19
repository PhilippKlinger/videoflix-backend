from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import validate_password
from .models import CustomUser
from django.core.exceptions import ValidationError


class UserRegistrationSerializer(serializers.ModelSerializer):
    password_confirm = serializers.CharField(style={'input_type': 'password'}, write_only=True)

    class Meta:
        model = CustomUser
        fields = ["email", "username", "password", "password_confirm"]
        extra_kwargs = {
            "password": {"write_only": True}
        }

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        validate_password(data['password'])
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm', None)  # Remove the password_confirm field
        user = CustomUser.objects.create_user(
            email=validated_data['email'],
            username=validated_data['username'],
            password=validated_data['password'],
        )
        user.set_password(validated_data['password'])
        user.save()
        return user

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, style={"input_type": "password"}, write_only=True)

    def validate(self, data):
        email = data.get("email")
        password = data.get("password")

        if email and password:
            # Benutzer über die E-Mail-Adresse authentifizieren
            user = authenticate(request=self.context.get('request'), username=email, password=password)
            if not user:
                raise serializers.ValidationError("Unable to log in with provided credentials.")
        else:
            raise serializers.ValidationError("Must include 'email' and 'password'.")

        # Überprüfen, ob der Benutzer aktiv ist
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive. Please activate Account first.")

        data['user'] = user
        return data
