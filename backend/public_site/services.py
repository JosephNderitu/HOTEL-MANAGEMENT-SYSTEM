from django.db.models import Q
from .models import Booking


def get_available_rooms(room_type, check_in, check_out):
    """Returns how many rooms of this type are free for the given date range."""
    overlapping = Booking.objects.filter(
        room_type=room_type,
        status__in=['pending', 'confirmed', 'checked_in'],
    ).filter(
        Q(check_in__lt=check_out) & Q(check_out__gt=check_in)
    ).count()
    return max(room_type.total_rooms - overlapping, 0)

from .models import MenuItem
def split_order_items_by_department(items):
    """items: list of dicts like {'id': <menu_item_id>, 'tier': ..., 'qty': ...}.
    Returns (kitchen_items, bar_items) — same shape as input, split by where each
    item is actually served from. A bar drink only ever goes in bar_items; everything
    else (food, kitchen-served drinks) goes in kitchen_items."""
    kitchen_items, bar_items = [], []
    for item in items:
        try:
            menu_item = MenuItem.objects.get(id=item['id'])
        except (MenuItem.DoesNotExist, KeyError):
            continue
        if menu_item.item_type == 'drink' and menu_item.serving_point == 'bar':
            bar_items.append(item)
        else:
            kitchen_items.append(item)
    return kitchen_items, bar_items