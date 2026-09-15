import json
from django import forms
from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.urls import path
from django.http import JsonResponse



# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    'pending': '#C9A227',
    'confirmed': '#0B6B3A',
    'checked_in': '#0B6B3A',
    'checked_out': '#6b7280',
    'cancelled': '#dc2626',
}


def status_badge(status, display):
    color = STATUS_COLORS.get(status, '#6b7280')
    return format_html(
        '<span style="background:{}; color:white; padding:3px 10px; '
        'border-radius:9999px; font-size:11px; font-weight:600;">{}</span>',
        color, display,
    )


def image_thumb(image_field, size=48):
    if image_field:
        return format_html(
            '<img src="{}" style="width:{}px; height:{}px; object-fit:cover; '
            'border-radius:6px;" />',
            image_field.url, size, size,
        )
    return "—"


# ---------------------------------------------------------------------------
# RoomType (+ up to 4 images, inline)
# ---------------------------------------------------------------------------

class RoomTypeImageInline(admin.TabularInline):
    model = RoomTypeImage
    extra = 1
    max_num = 4
    fields = ('image', 'preview')
    readonly_fields = ('preview',)

    @admin.display(description='Preview')
    def preview(self, obj):
        return image_thumb(obj.image, size=60)


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'price_range', 'capacity', 'room_count_display', 'is_active')
    list_display_links = ('name',)
    list_editable = ('is_active',)
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('price_min',)
    inlines = [RoomTypeImageInline]
    change_form_template = 'admin/public_site/roomtype/change_form.html'
    fieldsets = (
        ('Room Details', {'fields': ('name', 'slug', 'description')}),
        ('Pricing & Capacity', {'fields': (('price_min', 'price_max'), 'capacity', 'is_active')}),
    )

    @admin.display(description='Photo')
    def thumbnail(self, obj):
        first = obj.images.first()
        return image_thumb(first.image if first else None)

    @admin.display(description='Price Range')
    def price_range(self, obj):
        return f"KSh {obj.price_min:,.0f} – {obj.price_max:,.0f}"

    @admin.display(description='Rooms')
    def room_count_display(self, obj):
        return f"{obj.available_rooms_count} available / {obj.total_rooms} total"

    def get_urls(self):
        custom = [
            path('<int:roomtype_id>/bulk-add-rooms/', self.admin_site.admin_view(self.bulk_add_rooms), name='public_site_roomtype_bulk_add_rooms'),
            path('<int:roomtype_id>/delete-room/<int:room_id>/', self.admin_site.admin_view(self.delete_room), name='public_site_roomtype_delete_room'),
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url='', extra_context=None):
        room_type = self.get_object(request, object_id)
        extra_context = extra_context or {}
        extra_context['room_type'] = room_type
        extra_context['rooms'] = room_type.rooms.order_by('number') if room_type else []
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def bulk_add_rooms(self, request, roomtype_id):
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)
        room_type = self.get_object(request, roomtype_id)
        if not room_type:
            return JsonResponse({'error': 'Room type not found'}, status=404)

        try:
            data = json.loads(request.body)
            start = int(data.get('start'))
            end = int(data.get('end'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Start and end must be whole numbers, e.g. 101 to 120.'}, status=400)

        if end < start:
            return JsonResponse({'error': 'End number must not be less than start number.'}, status=400)
        if (end - start) > 200:
            return JsonResponse({'error': 'That range is too large to create in one go (max 200 rooms at a time).'}, status=400)

        created, skipped = [], []
        for num in range(start, end + 1):
            number_str = str(num)
            if Room.objects.filter(number=number_str).exists():
                skipped.append(number_str)
                continue
            room = Room.objects.create(room_type=room_type, number=number_str, status='available')
            created.append({'id': room.id, 'number': room.number, 'status': room.status})

        return JsonResponse({
            'success': True,
            'created': created,
            'skipped': skipped,
            'total_rooms': room_type.total_rooms,
            'available_count': room_type.available_rooms_count,
        })

    def delete_room(self, request, roomtype_id, room_id):
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)
        room = Room.objects.filter(id=room_id, room_type_id=roomtype_id).first()
        if not room:
            return JsonResponse({'error': 'Room not found'}, status=404)
        if room.status == 'occupied':
            return JsonResponse({'error': 'Cannot remove a room that is currently occupied.'}, status=400)
        room.delete()
        room_type = self.get_object(request, roomtype_id)
        return JsonResponse({
            'success': True,
            'total_rooms': room_type.total_rooms,
            'available_count': room_type.available_rooms_count,
        })


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('number', 'room_type', 'status_badge')
    list_filter = ('room_type', 'status')
    list_editable = ()
    search_fields = ('number',)
    ordering = ('room_type', 'number')

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colors = {'available': '#0B6B3A', 'occupied': '#C9A227', 'cleaning': '#3b82f6', 'maintenance': '#dc2626'}
        color = colors.get(obj.status, '#6b7280')
        text_color = '#000' if obj.status == 'occupied' else '#fff'
        return format_html(
            '<span style="background:{}; color:{}; padding:3px 10px; border-radius:9999px; font-size:11px; font-weight:700;">{}</span>',
            color, text_color, obj.get_status_display().upper(),
        )

# ---------------------------------------------------------------------------
# ConferenceRoom
# ---------------------------------------------------------------------------

@admin.register(ConferenceRoom)
class ConferenceRoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'tier_badge', 'price_range', 'capacity', 'is_active')
    list_editable = ('is_active',)
    list_filter = ('tier', 'is_active')
    search_fields = ('name', 'description')
    ordering = ('tier', 'name')

    @admin.display(description='Tier')
    def tier_badge(self, obj):
        color = '#C9A227' if obj.tier == 'vip' else '#0B6B3A'
        text_color = '#000' if obj.tier == 'vip' else '#fff'
        return format_html(
            '<span style="background:{}; color:{}; padding:3px 10px; '
            'border-radius:9999px; font-size:11px; font-weight:700;">{}</span>',
            color, text_color, obj.get_tier_display().upper(),
        )

    @admin.display(description='Price Range')
    def price_range(self, obj):
        return f"KSh {obj.price_min:,.0f} – {obj.price_max:,.0f}"


# ---------------------------------------------------------------------------
# Booking — dropdowns show the valid price range right in the label
# ---------------------------------------------------------------------------

class BookingAdminForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room_type'].label_from_instance = lambda rt: (
            f"{rt.name} (KSh {rt.price_min:,.0f} – {rt.price_max:,.0f})"
        )
        self.fields['conference_room'].label_from_instance = lambda cr: (
            f"{cr.name} — {cr.get_tier_display()} (KSh {cr.price_min:,.0f} – {cr.price_max:,.0f})"
        )
        self.fields['amount'].help_text = (
            "Must fall within the selected room type or conference room's price range."
        )


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    form = BookingAdminForm
    list_display = (
        'guest_name', 'booking_type', 'target_display', 'check_in', 'check_out',
        'source_badge', 'status_display', 'amount', 'created_at',
    )
    list_filter = ('booking_type', 'status', 'source', 'room_type', 'conference_room')
    search_fields = ('guest_name', 'guest_phone', 'guest_id_no')
    date_hierarchy = 'check_in'
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    autocomplete_fields = ('room_type', 'conference_room')
    actions = ['mark_confirmed', 'mark_checked_in', 'mark_checked_out', 'mark_cancelled']

    fieldsets = (
        ('Guest Details', {'fields': ('guest_name', 'guest_phone', 'guest_id_no')}),
        ('Booking Type', {'fields': ('booking_type', 'room_type', 'conference_room')}),
        ('Stay / Event Details', {'fields': ('check_in', 'check_out', 'amount')}),
        ('Status & Source', {'fields': ('status', 'source', 'created_at')}),
    )

    @admin.display(description='Room / Conference')
    def target_display(self, obj):
        return obj.room_type or obj.conference_room

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    @admin.display(description='Source')
    def source_badge(self, obj):
        icon = {'online': '🌐', 'phone': '📞', 'walkin': '🚶'}.get(obj.source, '')
        return format_html('{} {}', icon, obj.get_source_display())

    @admin.action(description='Mark selected bookings as Confirmed')
    def mark_confirmed(self, request, queryset):
        queryset.update(status='confirmed')

    @admin.action(description='Mark selected bookings as Checked In')
    def mark_checked_in(self, request, queryset):
        queryset.update(status='checked_in')

    @admin.action(description='Mark selected bookings as Checked Out')
    def mark_checked_out(self, request, queryset):
        queryset.update(status='checked_out')

    @admin.action(description='Cancel selected bookings')
    def mark_cancelled(self, request, queryset):
        queryset.update(status='cancelled')


# ---------------------------------------------------------------------------
# MenuItem — one row, two prices, up to 4 images
# ---------------------------------------------------------------------------

class MenuItemImageInline(admin.TabularInline):
    model = MenuItemImage
    extra = 1
    max_num = 4
    fields = ('image', 'preview')
    readonly_fields = ('preview',)

    @admin.display(description='Preview')
    def preview(self, obj):
        return image_thumb(obj.image, size=60)


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'item_type', 'serving_point', 'category', 'cost_price', 'regular_price', 'margin_display', 'stock_display', 'is_available')
    list_editable = ('cost_price', 'regular_price', 'is_available')
    list_display_links = ('name',)
    list_filter = ('item_type', 'category', 'is_available', 'serving_point')
    search_fields = ('name', 'category', 'description')
    ordering = ('item_type', 'name')
    inlines = [MenuItemImageInline]

    fieldsets = (
        ('Item Details', {'fields': ('name', 'item_type', 'category', 'course', 'serving_point', 'is_quick_serve', 'description')}),
        ('Pricing & Availability', {'fields': (('cost_price', 'regular_price', 'vip_price'), 'is_available')}),
    )

    @admin.display(description='Margin')
    def margin_display(self, obj):
        profit = obj.profit_per_unit
        if profit is None:
            return format_html('<span style="color:#9ca3af;">—</span>')
        pct = (profit / obj.regular_price * 100) if obj.regular_price else 0
        color = '#0B6B3A' if profit > 0 else '#dc2626'
        return format_html('<span style="color:{}; font-weight:600;">KSh {} ({}%)</span>', color, f"{profit:,.0f}", f"{pct:.0f}")

    @admin.display(description='Stock')
    def stock_display(self, obj):
        stock = getattr(obj, 'stock_item', None)
        if stock is None:
            if obj.requires_stock_link:
                return format_html('<span style="color:#dc2626; font-weight:700;">NOT LINKED</span>')
            return format_html('<span style="color:#9ca3af;">Not tracked</span>')
        color = '#dc2626' if stock.is_low_stock else '#0B6B3A'
        return format_html('<span style="color:{}; font-weight:600;">{} {}</span>', color, f"{stock.quantity_on_hand:g}", stock.unit)
    
    @admin.display(description='Photo')
    def thumbnail(self, obj):
        first = obj.images.first()
        return image_thumb(first.image if first else None)


# ---------------------------------------------------------------------------
# Order (inline items, each item can be billed at regular or VIP rate)
# ---------------------------------------------------------------------------

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ('menu_item',)
    fields = ('menu_item', 'tier', 'quantity', 'line_total')
    readonly_fields = ('line_total',)

    @admin.display(description='Line Total')
    def line_total(self, obj):
        if obj.menu_item_id:
            return f"KSh {obj.unit_price * obj.quantity:,.0f}"
        return "—"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'customer_phone', 'status_display', 'order_total', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('customer_name', 'customer_phone')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    inlines = [OrderItemInline]
    actions = ['mark_confirmed', 'mark_cancelled']

    fieldsets = (
        ('Customer', {'fields': ('customer_name', 'customer_phone', 'notes')}),
        ('Status', {'fields': ('status', 'created_at')}),
    )

    @admin.display(description='Status', ordering='status')
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())

    @admin.display(description='Order Total')
    def order_total(self, obj):
        total = sum(item.unit_price * item.quantity for item in obj.items.all())
        return f"KSh {total:,.0f}"

    @admin.action(description='Mark selected orders as Confirmed')
    def mark_confirmed(self, request, queryset):
        queryset.update(status='confirmed')

    @admin.action(description='Cancel selected orders')
    def mark_cancelled(self, request, queryset):
        queryset.update(status='cancelled')


# ---------------------------------------------------------------------------
# ContactMessage — read-only inbox
# ---------------------------------------------------------------------------
    
import json
from django.contrib import admin
from django.urls import path
from django.http import JsonResponse
from .models import Conversation, ChatMessage


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'message_count', 'last_message_display')
    search_fields = ('email', 'name')
    ordering = ('-last_message_at',)  # most recently active conversation first
    change_form_template = 'admin/public_site/conversation/change_form.html'
    fields = ('email', 'name')

    @admin.display(description='Messages')
    def message_count(self, obj):
        return obj.messages.count()

    @admin.display(description='Last Activity', ordering='last_message_at')
    def last_message_display(self, obj):
        return obj.last_message_at.strftime('%b %d, %Y %H:%M')

    def get_urls(self):
        custom = [
            path(
                '<int:conversation_id>/send-reply/',
                self.admin_site.admin_view(self.send_reply),
                name='public_site_conversation_send_reply',
            ),
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url='', extra_context=None):
        conversation = self.get_object(request, object_id)
        extra_context = extra_context or {}
        extra_context['conversation'] = conversation
        extra_context['chat_messages'] = (
            conversation.messages.order_by('created_at') if conversation else []  # chronological, oldest first
        )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def send_reply(self, request, conversation_id):
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)
        if not self.has_change_permission(request):
            return JsonResponse({'error': 'Permission denied'}, status=403)

        conversation = self.get_object(request, conversation_id)
        if not conversation:
            return JsonResponse({'error': 'Conversation not found'}, status=404)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid request'}, status=400)

        body = data.get('body', '').strip()
        if not body:
            return JsonResponse({'error': 'Message cannot be empty'}, status=400)

        msg = ChatMessage.objects.create(conversation=conversation, sender='staff', body=body)

        return JsonResponse({
            'success': True,
            'message': {
                'sender': msg.sender,
                'body': msg.body,
                'created_at': msg.created_at.strftime('%b %d, %H:%M'),
            },
        })
        
from .models import Table


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ('number', 'capacity', 'status_badge', 'is_vip')
    list_filter = ('status', 'is_vip')
    change_list_template = 'admin/public_site/table/change_list.html'

    @admin.display(description='Status')
    def status_badge(self, obj):
        colors = {'available': '#0B6B3A', 'occupied': '#C9A227', 'needs_cleaning': '#3b82f6'}
        color = colors.get(obj.status, '#6b7280')
        text_color = '#000' if obj.status == 'occupied' else '#fff'
        return format_html(
            '<span style="background:{}; color:{}; padding:3px 10px; border-radius:9999px; font-size:11px; font-weight:700;">{}</span>',
            color, text_color, obj.get_status_display().upper(),
        )

    def get_urls(self):
        custom = [path('bulk-add/', self.admin_site.admin_view(self.bulk_add), name='public_site_table_bulk_add')]
        return custom + super().get_urls()

    def bulk_add(self, request):
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)
        try:
            data = json.loads(request.body)
            start, end, capacity = int(data['start']), int(data['end']), int(data.get('capacity', 4))
        except (KeyError, ValueError, TypeError):
            return JsonResponse({'error': 'Enter valid start, end, and capacity numbers.'}, status=400)
        if end < start or (end - start) > 100:
            return JsonResponse({'error': 'Invalid range (max 100 tables at a time).'}, status=400)

        created, skipped = [], []
        for num in range(start, end + 1):
            if Table.objects.filter(number=str(num)).exists():
                skipped.append(str(num))
                continue
            t = Table.objects.create(number=str(num), capacity=capacity)
            created.append(t.number)
        return JsonResponse({'success': True, 'created': created, 'skipped': skipped})
    
@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ('guest_name', 'party_size', 'status', 'wait_minutes', 'created_at')
    list_filter = ('status',)
    search_fields = ('guest_name', 'phone_number')
    readonly_fields = ('created_at', 'seated_at')
    

@admin.register(BarTab)
class BarTabAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'phone_number', 'status', 'opened_at', 'closed_at')
    list_filter = ('status',)
    search_fields = ('customer_name', 'phone_number')


@admin.register(HappyHourWindow)
class HappyHourWindowAdmin(admin.ModelAdmin):
    list_display = ('label', 'start_time', 'end_time', 'discount_percent', 'is_active', 'currently_active')
    list_editable = ('is_active',)

    @admin.display(description='Active Now', boolean=True)
    def currently_active(self, obj):
        return obj.is_now()