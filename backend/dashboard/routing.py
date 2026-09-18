from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/reception/$', consumers.ReceptionConsumer.as_asgi()),
    re_path(r'^ws/kitchen/$', consumers.KitchenConsumer.as_asgi()),
    re_path(r'^ws/store/$', consumers.StoreConsumer.as_asgi()),
]