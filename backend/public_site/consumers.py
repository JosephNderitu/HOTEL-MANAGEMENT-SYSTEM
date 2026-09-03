import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Conversation, ChatMessage, group_name_for_email


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.email = await self.get_session_email()
        if not self.email:
            await self.close()
            return
        self.group_name = group_name_for_email(self.email)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        body = data.get('body', '').strip()
        if not body:
            return
        message = await self.save_message(body)
        await self.channel_layer.group_send(self.group_name, {
            'type': 'chat_message',
            'message': message,
        })

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event['message']))

    @database_sync_to_async
    def get_session_email(self):
        return self.scope['session'].get('contact_email')

    @database_sync_to_async
    def save_message(self, body):
        conversation, _ = Conversation.objects.get_or_create(email=self.email)
        msg = ChatMessage.objects.create(conversation=conversation, sender='guest', body=body)
        return {'sender': msg.sender, 'body': msg.body, 'created_at': msg.created_at.isoformat()}