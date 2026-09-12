import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from public_site.routing import websocket_urlpatterns as public_ws_patterns
from dashboard.routing import websocket_urlpatterns as dashboard_ws_patterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(URLRouter(public_ws_patterns + dashboard_ws_patterns)),
})