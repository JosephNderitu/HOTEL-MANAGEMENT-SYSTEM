import io
from PIL import Image
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import models
import random
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_IMAGES_PER_ITEM = 4

def group_name_for_email(email):
    return f"chat_{email.replace('@', '_at_').replace('.', '_dot_')}"

def compress_image_if_needed(image_field):
    """If an uploaded image exceeds 10MB, re-encode it down under that limit."""
    if image_field.size <= MAX_IMAGE_SIZE_BYTES:
        return image_field

    img = Image.open(image_field)
    img_format = img.format or 'JPEG'
    if img.mode in ('RGBA', 'P') and img_format == 'JPEG':
        img = img.convert('RGB')

    quality = 90
    buffer = io.BytesIO()
    img.save(buffer, format=img_format, quality=quality, optimize=True)

    while buffer.tell() > MAX_IMAGE_SIZE_BYTES and quality > 20:
        quality -= 10
        buffer.seek(0)
        buffer.truncate()
        img.save(buffer, format=img_format, quality=quality, optimize=True)

    buffer.seek(0)
    return ContentFile(buffer.read(), name=image_field.name)


class RoomType(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    price_min = models.DecimalField(max_digits=10, decimal_places=2)
    price_max = models.DecimalField(max_digits=10, decimal_places=2)
    capacity = models.PositiveIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if self.price_min and self.price_max and self.price_min > self.price_max:
            raise ValidationError("Minimum price cannot be greater than maximum price.")

    @property
    def total_rooms(self):
        return self.rooms.count()

    @property
    def available_rooms_count(self):
        return self.rooms.filter(status='available').count()

    def __str__(self):
        return self.name


class RoomTypeImage(models.Model):
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='room_types/')

    def clean(self):
        if self.room_type_id:
            existing = self.room_type.images.exclude(pk=self.pk).count()
            if existing >= MAX_IMAGES_PER_ITEM:
                raise ValidationError(f"A room type can have at most {MAX_IMAGES_PER_ITEM} images.")

    def save(self, *args, **kwargs):
        if self.image and hasattr(self.image, 'file'):
            self.image = compress_image_if_needed(self.image)
        super().save(*args, **kwargs)

class Room(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('cleaning', 'Cleaning'),
        ('maintenance', 'Maintenance'),
    ]
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name='rooms')
    number = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    def __str__(self):
        return f"Room {self.number} ({self.room_type.name})"

class ConferenceRoom(models.Model):
    TIER_CHOICES = [
        ('regular', 'Regular'),
        ('vip', 'VIP'),
    ]
    name = models.CharField(max_length=100)
    tier = models.CharField(max_length=10, choices=TIER_CHOICES)
    description = models.TextField(blank=True)
    price_min = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    price_max = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    capacity = models.PositiveIntegerField(default=20)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if self.price_min and self.price_max and self.price_min > self.price_max:
            raise ValidationError("Minimum price cannot be greater than maximum price.")

    def __str__(self):
        return f"{self.name} ({self.get_tier_display()})"


class Booking(models.Model):
    BOOKING_TYPE_CHOICES = [
        ('room', 'Room'),
        ('conference', 'Conference'),
    ]
    SOURCE_CHOICES = [
        ('online', 'Online'),
        ('phone', 'Phone'),
        ('walkin', 'Walk-in'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('checked_in', 'Checked In'),
        ('checked_out', 'Checked Out'),
        ('cancelled', 'Cancelled'),
    ]

    booking_type = models.CharField(max_length=12, choices=BOOKING_TYPE_CHOICES, default='room')
    guest_name = models.CharField(max_length=150)
    guest_phone = models.CharField(max_length=20)
    guest_id_no = models.CharField(max_length=20, default='', help_text="National ID or Passport number")

    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name='bookings', null=True, blank=True)
    conference_room = models.ForeignKey(ConferenceRoom, on_delete=models.PROTECT, related_name='bookings', null=True, blank=True)
    assigned_room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings_history')

    check_in = models.DateField()
    check_out = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='online')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def nights(self):
        return (self.check_out - self.check_in).days

    @property
    def total_amount(self):
        if self.booking_type == 'room':
            return self.amount * self.nights
        return self.amount

    @property
    def amount_paid(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def balance_due(self):
        return self.total_amount - self.amount_paid
    
        
    @property
    def room_service_balance_due(self):
        return sum(o.balance_due for o in self.room_service_orders.filter(status='confirmed'))

    @property
    def total_balance_due(self):
        return self.balance_due + self.room_service_balance_due
    
    def clean(self):
        if self.booking_type == 'room':
            if not self.room_type_id:
                raise ValidationError("Select a room type for a room booking.")
            self.conference_room = None
            price_min, price_max = self.room_type.price_min, self.room_type.price_max
        elif self.booking_type == 'conference':
            if not self.conference_room_id:
                raise ValidationError("Select a conference room for a conference booking.")
            self.room_type = None
            price_min, price_max = self.conference_room.price_min, self.conference_room.price_max
        else:
            return

        if self.amount is not None and not (price_min <= self.amount <= price_max):
            raise ValidationError(
                f"Amount must be between KSh {price_min:,.0f} and KSh {price_max:,.0f} for the selected option."
            )

    def __str__(self):
        target = self.room_type or self.conference_room
        return f"{self.guest_name} — {target} ({self.check_in} to {self.check_out})"


class Table(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('needs_cleaning', 'Needs Cleaning'),
    ]
    number = models.CharField(max_length=20, unique=True)
    capacity = models.PositiveIntegerField(default=4)
    is_vip = models.BooleanField(default=False, help_text="VIP dining area. New orders here default to VIP pricing.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    @property
    def open_orders(self):
        return [o for o in self.orders.exclude(status='cancelled').order_by('-created_at') if o.balance_due > 0]
    def __str__(self):
        return f"Table {self.number}"

class WaitlistEntry(models.Model):
    STATUS_CHOICES = [
        ('waiting', 'Waiting'),
        ('seated', 'Seated'),
        ('cancelled', 'Cancelled'),
    ]
    guest_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, blank=True)
    party_size = models.PositiveIntegerField(default=2)
    notes = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='waiting')
    created_at = models.DateTimeField(auto_now_add=True)
    seated_at = models.DateTimeField(null=True, blank=True)

    @property
    def wait_minutes(self):
        end = self.seated_at or timezone.now()
        return int((end - self.created_at).total_seconds() // 60)

    def __str__(self):
        return f"{self.guest_name} ({self.party_size}) — {self.get_status_display()}"
    

class MenuItem(models.Model):
    ITEM_TYPE_CHOICES = [
        ('food', 'Food'),
        ('drink', 'Drink'),
    ]
    COURSE_CHOICES = [
        ('starter', 'Starter'),
        ('main', 'Main Course'),
        ('dessert', 'Dessert'),
    ]
    SERVING_POINT_CHOICES = [
        ('kitchen', 'Kitchen (Restaurant)'),
        ('bar', 'Bar'),
    ]
    serving_point = models.CharField(
        max_length=10, choices=SERVING_POINT_CHOICES, default='kitchen',
        help_text="For drinks: where this is served from. Tea/coffee/water/soft drinks = Kitchen. Beer/spirits/wine/cocktails = Bar."
    )
    name = models.CharField(max_length=150)
    item_type = models.CharField(max_length=10, choices=ITEM_TYPE_CHOICES)
    category = models.CharField(max_length=100, blank=True)
    regular_price = models.DecimalField(max_digits=10, decimal_places=2)
    vip_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Leave blank if this item has no separate VIP price"
    )
    course = models.CharField(max_length=10, choices=COURSE_CHOICES, default='main')
    is_quick_serve = models.BooleanField(
        default=False,
        help_text="Already made / doesn't need active cooking (e.g. bottled drinks, pre-made snacks). Skips the 'Preparing' step in the kitchen queue."
    )
    description = models.TextField(blank=True)
    is_available = models.BooleanField(default=True)

    @property
    def is_in_stock(self):
        stock = getattr(self, 'stock_item', None)
        if stock is None:
            return True  # not stock-tracked, e.g. most kitchen dishes, always orderable
        return stock.quantity_on_hand > 0

    @property
    def is_orderable(self):
        return self.is_available and self.is_in_stock
    
    def clean(self):
        if self.vip_price is not None and self.vip_price < self.regular_price:
            raise ValidationError("VIP price should not be lower than the regular price.")

    def __str__(self):
        return self.name


class MenuItemImage(models.Model):
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='menu_items/')

    def clean(self):
        if self.menu_item_id:
            existing = self.menu_item.images.exclude(pk=self.pk).count()
            if existing >= MAX_IMAGES_PER_ITEM:
                raise ValidationError(f"A menu item can have at most {MAX_IMAGES_PER_ITEM} images.")

    def save(self, *args, **kwargs):
        if self.image and hasattr(self.image, 'file'):
            self.image = compress_image_if_needed(self.image)
        super().save(*args, **kwargs)


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]
    ORDER_TYPE_CHOICES = [
        ('dine_in', 'Dine In'),
        ('takeaway', 'Takeaway'),
        ('conference', 'Conference Catering'),
        ('event', 'Event / Bulk Order'),
    ]
    order_type = models.CharField(max_length=12, choices=ORDER_TYPE_CHOICES, default='dine_in')
    served_at = models.CharField(max_length=150, blank=True, help_text="For conference/event orders: location or event name")
    party_size = models.PositiveIntegerField(null=True, blank=True, help_text="Number of guests, for event/bulk orders")
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    room_booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='room_service_orders')
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')

    @property
    def total_amount(self):
        return sum(item.unit_price * item.quantity for item in self.items.all() if not item.is_cancelled)

    @property
    def amount_paid(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def balance_due(self):
        return self.total_amount - self.amount_paid
    
    @property
    def is_open(self):
        """An order still 'sitting' at a table: not cancelled, and not yet fully paid."""
        return self.status != 'cancelled' and self.balance_due > 0
    
    @property
    def is_fully_served(self):
        active_items = [i for i in self.items.all() if not i.is_cancelled]
        if not active_items:
            return False
        return all(i.prep_status == 'served' for i in active_items)
    
    def __str__(self):
        return f"Order #{self.id} — {self.customer_name}"


class OrderItem(models.Model):
    PREP_STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready to Serve'),
        ('served', 'Served'),
    ]
    prep_status = models.CharField(max_length=10, choices=PREP_STATUS_CHOICES, default='queued')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    tier = models.CharField(max_length=10, choices=[('regular', 'Regular'), ('vip', 'VIP')], default='regular')
    quantity = models.PositiveIntegerField(default=1)
    is_cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=200, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items_cancelled')
    cancelled_at = models.DateTimeField(null=True, blank=True)

    @property
    def unit_price(self):
        if self.tier == 'vip' and self.menu_item.vip_price is not None:
            return self.menu_item.vip_price
        return self.menu_item.regular_price

    def __str__(self):
        return f"{self.quantity} x {self.menu_item.name} ({self.get_tier_display()})"


#######Mesaaging/contact models for public site (guest-facing) #######

class Conversation(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation with {self.email}"


class ChatMessage(models.Model):
    SENDER_CHOICES = [('guest', 'Guest'), ('staff', 'Staff')]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    notified = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender}: {self.body[:30]}"


class EmailVerification(models.Model):
    email = models.EmailField()
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=10)

    def __str__(self):
        return f"{self.email} — {self.code}"


from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


@receiver(post_save, sender=ChatMessage)
def notify_guest_on_staff_reply(sender, instance, created, **kwargs):
    if not created or instance.sender != 'staff':
        return

    # Push it live over WebSocket first, this is what makes it feel instant.
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        group_name_for_email(instance.conversation.email),
        {
            'type': 'chat_message',
            'message': {
                'sender': instance.sender,
                'body': instance.body,
                'created_at': instance.created_at.isoformat(),
            },
        },
    )

    # Still email them too, in case they've closed the tab entirely.
    if not instance.notified:
        send_mail(
            subject="New reply from Queens Garden Hotel",
            message=(
                f"Hi {instance.conversation.name or ''},\n\n"
                f"You have a new reply from Queens Garden Hotel:\n\n"
                f"\"{instance.body}\"\n\n"
                f"Visit {settings.SITE_URL}/contact/ and verify with this email "
                f"to continue the conversation.\n\n"
                f"Queens Garden Hotel"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.conversation.email],
            fail_silently=True,
        )
        instance.notified = True
        instance.save(update_fields=['notified'])
        
@receiver(post_save, sender=Booking)
def notify_reception_new_booking(sender, instance, created, **kwargs):
    if not created or instance.source != 'online':
        return  # only guest-made bookings are "new" to reception, not staff's own walk-in entries
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)('reception_updates', {
        'type': 'reception_notification',
        'payload': {
            'kind': 'booking',
            'message': f"New online booking from {instance.guest_name}",
        },
    })


@receiver(post_save, sender=Order)
def notify_reception_new_order(sender, instance, created, **kwargs):
    if not created:
        return
    room_note = ""
    if instance.room_booking and instance.room_booking.assigned_room:
        room_note = f" (Room {instance.room_booking.assigned_room.number} service)"
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)('reception_updates', {
        'type': 'reception_notification',
        'payload': {
            'kind': 'order',
            'message': f"New order from {instance.customer_name}{room_note}",
        },
    })