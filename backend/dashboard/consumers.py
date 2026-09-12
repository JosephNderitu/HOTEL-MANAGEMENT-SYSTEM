import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .permissions import can_access


class ReceptionConsumer(AsyncWebsocketConsumer):
    group_name = 'reception_updates'

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return
        allowed = await database_sync_to_async(can_access)(user, 'reception')
        if not allowed:
            await self.close()
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def reception_notification(self, event):
        await self.send(text_data=json.dumps(event['payload']))