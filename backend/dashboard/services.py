from django.db import transaction
from store.models import StockItem


def confirm_order_and_deduct_stock(order):
    """Confirms an order, deducting stock for every linked item. Returns (success, shortfall_names)."""
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
            # Ready-made items skip straight to "ready", nothing to actively cook.
            order_item.prep_status = 'ready' if order_item.menu_item.is_quick_serve else 'queued'
            order_item.save(update_fields=['prep_status'])

        order.status = 'confirmed'
        order.save(update_fields=['status'])
        return True, []