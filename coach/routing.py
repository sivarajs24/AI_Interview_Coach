from django.urls import re_path

from .consumers import EmotionConsumer


websocket_urlpatterns = [re_path(r"^ws/socket/$", EmotionConsumer.as_asgi())]