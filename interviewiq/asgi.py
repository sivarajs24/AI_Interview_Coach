"""ASGI entry point for HTTP and live emotion websocket traffic."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interviewiq.settings")

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from coach.routing import websocket_urlpatterns

django_application = get_asgi_application()
application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)