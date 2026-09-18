from django.db import transaction
from store.models import StockItem
from .models import Payment

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import uuid
from decimal import Decimal

def confirm_order_and_deduct_stock(order):
    with transaction.atomic():
        shortfalls = []
        for order_item in order.items.select_related('menu_item__stock_item'):
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock and stock.quantity_on_hand < order_item.quantity:
                shortfalls.append(order_item.menu_item.name)

        if shortfalls:
            return False, shortfalls

        for order_item in order.items.select_related('menu_item__stock_item'):
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(
                    quantity_on_hand=stock.quantity_on_hand - order_item.quantity
                )
                from store.services import notify_low_stock_if_needed
                notify_low_stock_if_needed(stock)

            order_item.prep_status = 'ready' if order_item.menu_item.is_quick_serve else 'queued'
            order_item.save(update_fields=['prep_status'])

        order.status = 'confirmed'
        order.save(update_fields=['status'])

    if order.has_kitchen_items:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)('kitchen_updates', {
            'type': 'kitchen_notification',
            'payload': {
                'order_id': order.id,
                'customer_name': order.customer_name,
                'is_vip': order.has_vip_kitchen_item,
                'source': order.table.number if order.table_id else order.get_order_type_display(),
            },
        })

    return True, []

def settle_booking_stay(booking, amount, amount_tendered, method, reference, user):
    debts = []
    if booking.balance_due > 0:
        debts.append(('booking', booking, booking.balance_due))
    for order in booking.room_service_orders.filter(status='confirmed').order_by('created_at'):
        if order.balance_due > 0:
            debts.append(('order', order, order.balance_due))

    group_id = uuid.uuid4()
    remaining = amount
    created = []

    with transaction.atomic():
        for kind, target, owed in debts:
            if remaining <= 0:
                break
            pay_now = min(owed, remaining)
            payment = Payment(amount=pay_now, method=method, reference=reference,
                               received_by=user, settlement_group=group_id)
            if kind == 'booking':
                payment.booking = target
            else:
                payment.order = target
            payment.save()
            created.append(payment)
            remaining -= pay_now

        change = Decimal('0')
        if amount_tendered is not None:
            change = max(amount_tendered - amount, Decimal('0'))
        if change > 0 and created:
            last = created[-1]
            last.amount_tendered = last.amount + change
            last.save(update_fields=['amount_tendered'])

    return amount - remaining, change