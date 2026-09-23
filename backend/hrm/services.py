from math import radians, sin, cos, sqrt, atan2
from django.conf import settings


def is_within_geofence(lat, lng):
    if lat is None or lng is None:
        return False
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat, lng, settings.HOTEL_LATITUDE, settings.HOTEL_LONGITUDE])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a)) <= settings.HOTEL_GEOFENCE_RADIUS_METERS