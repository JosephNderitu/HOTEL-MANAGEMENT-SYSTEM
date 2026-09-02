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