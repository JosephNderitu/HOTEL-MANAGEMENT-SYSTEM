from decimal import Decimal
from .models import PurchaseLog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


RANGE_PRESETS = {
    'today': 0, 'week': 7, 'month': 30, 'quarter': 90, 'half_year': 182, 'year': 365,
}


def get_purchases_in_range(date_from, date_to, department=None):
    qs = PurchaseLog.objects.filter(purchased_at__date__gte=date_from, purchased_at__date__lte=date_to)
    if department:
        qs = qs.filter(department=department)
    return qs.select_related('stock_item', 'purchased_by').prefetch_related('receipts').order_by('department', '-purchased_at')


def summarize_by_department(purchases):
    summary = {}
    for p in purchases:
        d = summary.setdefault(p.department, {
            'label': p.get_department_display(), 'items': [],
            'total_excl': Decimal('0'), 'total_vat': Decimal('0'), 'total_incl': Decimal('0'),
        })
        d['items'].append(p)
        d['total_excl'] += p.amount_excl_vat
        d['total_vat'] += p.vat_amount
        d['total_incl'] += p.amount_incl_vat
    return summary

def notify_low_stock_if_needed(stock_item):
    stock_item.refresh_from_db()
    if stock_item.stock_status == 'ok':
        return
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)('store_alerts', {
        'type': 'store_alert',
        'payload': {
            'name': stock_item.name,
            'department': stock_item.get_department_display(),
            'quantity': f"{stock_item.quantity_on_hand:g}",
            'unit': stock_item.get_unit_display(),
            'level': stock_item.stock_status,
        },
    })