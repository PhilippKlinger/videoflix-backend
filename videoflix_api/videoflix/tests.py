from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from .models import Video
from django.core.cache import cache

class BaseTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.user_data = {
            "username": "newtestuser",
            "email": "test@example.com",
            "password": "12345"
        }
        self.video = Video.objects.create(title="Test Video", description="test upload")
        self.upload_video_url = reverse('video-upload')
        self.register_url = reverse('register-user')
        self.login_url = reverse('login-user')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        

class UserTest(BaseTest):
    def test_register_user(self):
        response = self.client.post(self.register_url, self.user_data, format='json')
        self.assertEqual(response.status_code, 201)
    
        
    def test_login_user(self):
        self.client.post(self.register_url, self.user_data, format='json')
        response = self.client.post(self.login_url, self.user_data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue('token' in response.data)
        
class VideoUploadTest(BaseTest):
    def test_upload_video(self):
        video_data = {            
                    "title": "test",
                    "description": "test",
                    "uploaded_at": "2024-04-08",
                    }
        response = self.client.post(self.upload_video_url, video_data, format='json')
        self.assertEqual(response.status_code, 201)
        
    def test_upload_video_fail(self):
        video_data = {            
                    "title": "",
                    "description": "",
                    }
        response = self.client.post(self.upload_video_url, video_data, format='json')
        self.assertEqual(response.status_code, 400)
        
          
class VideoListTests(BaseTest):
    def test_videos_authenticated(self):
        response = self.client.get('/videos/')
        self.assertEqual(response.status_code, 200)
        
        
class CacheTest(TestCase):
    def test_redis_cache(self):
        cache.set('test_key', 'test_value', timeout=30)
        result = cache.get('test_key')
        self.assertEqual(result, 'test_value', "Der Cache hat den Wert nicht korrekt zurückgegeben.")
        cache.delete('test_key')