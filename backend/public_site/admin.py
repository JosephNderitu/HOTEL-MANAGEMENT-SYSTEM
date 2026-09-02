from django import forms
from django.contrib import admin
from django.utils.html import format_html
from .models import (
    RoomType, RoomTypeImage, ConferenceRoom, Booking,
    MenuItem, MenuItemImage, Order, OrderItem, ContactMessage,
)


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
    list_display = ('thumbnail', 'name', 'price_range', 'capacity', 'total_rooms', 'is_active')
    list_display_links = ('name',)
    list_editable = ('total_rooms', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('price_min',)
    inlines = [RoomTypeImageInline]
    fieldsets = (
        ('Room Details', {'fields': ('name', 'slug', 'description')}),
        ('Pricing & Capacity', {'fields': (('price_min', 'price_max'), 'capacity', 'total_rooms', 'is_active')}),
    )

    @admin.display(description='Photo')
    def thumbnail(self, obj):
        first = obj.images.first()
        return image_thumb(first.image if first else None)

    @admin.display(description='Price Range')
    def price_range(self, obj):
        return f"KSh {obj.price_min:,.0f} – {obj.price_max:,.0f}"


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
    list_display = ('thumbnail', 'name', 'item_type', 'category', 'regular_price', 'vip_price', 'is_available')
    list_display_links = ('name',)
    list_editable = ('regular_price', 'vip_price', 'is_available')
    list_filter = ('item_type', 'category', 'is_available')
    search_fields = ('name', 'category', 'description')
    ordering = ('item_type', 'name')
    inlines = [MenuItemImageInline]

    fieldsets = (
        ('Item Details', {'fields': ('name', 'item_type', 'category', 'description')}),
        ('Pricing & Availability', {'fields': (('regular_price', 'vip_price'), 'is_available')}),
    )

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

@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'short_message', 'created_at')
    search_fields = ('name', 'email', 'message')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    readonly_fields = ('name', 'email', 'phone', 'message', 'created_at')

    @admin.display(description='Message')
    def short_message(self, obj):
        return obj.message[:60] + ('…' if len(obj.message) > 60 else '')

    def has_add_permission(self, request):
        return False