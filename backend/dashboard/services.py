from django.db import transaction
from django.db.models import F, Sum, Q
from store.models import StockItem, DailyUsageLog, DailyUsageItem, WastageLog, StockFloatDay
from public_site.models import Order, OrderItem
from .models import Payment

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import uuid
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone


def compute_requirements_for_selection(menu_item, quantity):
    recipe = getattr(menu_item, 'recipe', None)
    if recipe:
        reqs = []
        for line in recipe.lines.select_related('stock_item').all():
            if not line.is_tracked:
                continue  # not yet in stock catalog — nothing to deduct, but still costed below
            per_unit_qty = line.quantity_in_stock_unit / recipe.yield_quantity
            reqs.append((line.stock_item, per_unit_qty * quantity))
        return reqs
    stock = getattr(menu_item, 'stock_item', None)
    if stock:
        return [(stock, Decimal(quantity))]
    return []


def _snapshot_cost_for(menu_item, quantity):
    """Full recipe cost for this quantity, tracked + untracked ingredients combined."""
    recipe = getattr(menu_item, 'recipe', None)
    if recipe and recipe.lines.exists():
        return (recipe.cost_per_yield * quantity).quantize(Decimal('0.01'))
    return None


def _aggregate_and_lock(needed_totals):
    locked_stock, shortfalls = {}, []
    for stock_id, (stock, total_qty) in needed_totals.items():
        locked = StockItem.objects.select_for_update().get(id=stock_id)
        if total_qty > locked.quantity_on_hand:
            shortfalls.append(
                f"{locked.name} (need {total_qty:g} {locked.get_unit_display()}, have {locked.quantity_on_hand:g})"
            )
        locked_stock[stock_id] = locked
    return locked_stock, shortfalls

def reserve_stock_and_create_items(order, selections):
    """selections: list of (menu_item, tier, quantity, unit_price_override).
    Kitchen-department ingredients are NEVER deducted here — they're tracked via the
    kitchen float ledger instead, since they already left the storeroom when logged as taken."""
    needed_totals = {}
    for menu_item, tier, qty, _ in selections:
        for stock, req_qty in compute_requirements_for_selection(menu_item, qty):
            if stock.department == 'kitchen':
                continue
            stock_obj, total = needed_totals.get(stock.id, (stock, Decimal('0')))
            needed_totals[stock.id] = (stock_obj, total + req_qty)

    with transaction.atomic():
        locked_stock, shortfalls = _aggregate_and_lock(needed_totals)
        if shortfalls:
            return [], shortfalls

        created = []
        for menu_item, tier, qty, price_override in selections:
            order_item = OrderItem.objects.create(
                order=order, menu_item=menu_item, tier=tier, quantity=qty,
                unit_price_override=price_override,
                prep_status='ready' if menu_item.is_quick_serve else 'queued',
            )
            for stock, req_qty in compute_requirements_for_selection(menu_item, qty):
                if stock.department == 'kitchen':
                    continue
                locked_stock[stock.id].quantity_on_hand -= req_qty
            order_item.recipe_cost_snapshot = _snapshot_cost_for(menu_item, qty)
            order_item.save(update_fields=['recipe_cost_snapshot'])
            created.append(order_item)

        for locked in locked_stock.values():
            locked.save(update_fields=['quantity_on_hand'])
            from store.services import notify_low_stock_if_needed
            notify_low_stock_if_needed(locked)

    return created, []

def reserve_stock_for_existing_items(order_items):
    """For orders whose OrderItems already exist (e.g. a pending order being confirmed)."""
    needed_totals = {}
    for oi in order_items:
        for stock, req_qty in compute_requirements_for_selection(oi.menu_item, oi.quantity):
            if stock.department == 'kitchen':
                continue
            stock_obj, total = needed_totals.get(stock.id, (stock, Decimal('0')))
            needed_totals[stock.id] = (stock_obj, total + req_qty)

    with transaction.atomic():
        locked_stock, shortfalls = _aggregate_and_lock(needed_totals)
        if shortfalls:
            return False, shortfalls

        for oi in order_items:
            for stock, req_qty in compute_requirements_for_selection(oi.menu_item, oi.quantity):
                if stock.department == 'kitchen':
                    continue
                locked_stock[stock.id].quantity_on_hand -= req_qty
            oi.recipe_cost_snapshot = _snapshot_cost_for(oi.menu_item, oi.quantity)
            oi.prep_status = 'ready' if oi.menu_item.is_quick_serve else 'queued'
            oi.save(update_fields=['recipe_cost_snapshot', 'prep_status'])

        for locked in locked_stock.values():
            locked.save(update_fields=['quantity_on_hand'])
            from store.services import notify_low_stock_if_needed
            notify_low_stock_if_needed(locked)

    return True, []

def restore_stock_for_order_item(order_item):
    """Reverses the deduction for one OrderItem. Kitchen ingredients were never deducted
    in real time, so there's nothing to give back for them — only bar/direct-link items."""
    reqs = compute_requirements_for_selection(order_item.menu_item, order_item.quantity)
    if not reqs:
        return
    with transaction.atomic():
        for stock, qty in reqs:
            if stock.department == 'kitchen':
                continue
            StockItem.objects.filter(id=stock.id).update(quantity_on_hand=F('quantity_on_hand') + qty)

def confirm_order_and_deduct_stock(order):
    with transaction.atomic():
        order_items = list(order.items.select_related('menu_item__stock_item', 'menu_item__recipe'))
        success, shortfalls = reserve_stock_for_existing_items(order_items)
        if not success:
            return False, shortfalls
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

def build_kitchen_reconciliation(department, date):
    """
    Compares ACTUAL stock withdrawn (DailyUsageItem, chef-declared) against
    THEORETICAL stock required by what was actually sold that day (recipes).
    """
    from .views import _local_day_bounds  # reuse your existing helper

    log = DailyUsageLog.objects.filter(department=department, date=date).first()
    actual_by_stock = {}
    if log:
        for item in log.items.select_related('stock_item'):
            if item.stock_item_id:
                actual_by_stock[item.stock_item_id] = actual_by_stock.get(item.stock_item_id, Decimal('0')) + item.quantity

    day_start, day_end = _local_day_bounds(date, date)
    orders = Order.objects.filter(
        status='confirmed', created_at__gte=day_start, created_at__lt=day_end
    ).prefetch_related('items__menu_item__recipe__lines__stock_item')

    theoretical_by_stock = {}
    for order in orders:
        if department == 'kitchen':
            items = order.kitchen_items()
        else:
            items = [i for i in order.items.all() if not i.is_cancelled
                     and i.menu_item.item_type == 'drink' and i.menu_item.serving_point == 'bar']
        for oi in items:
            for stock, qty in compute_requirements_for_selection(oi.menu_item, oi.quantity):
                theoretical_by_stock[stock.id] = theoretical_by_stock.get(stock.id, Decimal('0')) + qty

    all_ids = set(actual_by_stock) | set(theoretical_by_stock)
    stock_items = {s.id: s for s in StockItem.objects.filter(id__in=all_ids)}

    rows = []
    for sid in all_ids:
        stock = stock_items[sid]
        actual = actual_by_stock.get(sid, Decimal('0'))
        theoretical = theoretical_by_stock.get(sid, Decimal('0'))
        variance = actual - theoretical
        rows.append({
            'stock_item': stock, 'actual': actual, 'theoretical': theoretical,
            'variance': variance,
            'variance_pct': float(variance / theoretical * 100) if theoretical else None,
            'variance_cost': (variance * stock.last_unit_cost).quantize(Decimal('0.01')),
        })
    rows.sort(key=lambda r: abs(r['variance_cost']), reverse=True)

    total_variance_cost = sum((r['variance_cost'] for r in rows), Decimal('0'))
    return rows, total_variance_cost

def theoretical_consumption_by_stock(department, date):
    """{stock_id: qty} of ingredients that confirmed sales on this day say should have been used."""
    from .views import _local_day_bounds
    day_start, day_end = _local_day_bounds(date, date)
    orders = Order.objects.filter(
        status='confirmed', created_at__gte=day_start, created_at__lt=day_end
    ).prefetch_related('items__menu_item__recipe__lines__stock_item')

    theoretical = {}
    for order in orders:
        if department == 'kitchen':
            items = order.kitchen_items()
        else:
            items = [i for i in order.items.all() if not i.is_cancelled
                     and i.menu_item.item_type == 'drink' and i.menu_item.serving_point == 'bar']
        for oi in items:
            for stock, qty in compute_requirements_for_selection(oi.menu_item, oi.quantity):
                theoretical[stock.id] = theoretical.get(stock.id, Decimal('0')) + qty
    return theoretical

def _taken_amount_for_stock_day(stock_item, date):
    return DailyUsageItem.objects.filter(
        stock_item=stock_item, log__department='kitchen', log__date=date
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

def _wasted_amount_for_stock_day(stock_item, date):
    return WastageLog.objects.filter(
        stock_item=stock_item, logged_at__date=date
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

def _consumed_amount_for_stock_day(stock_item, date):
    return theoretical_consumption_by_stock('kitchen', date).get(stock_item.id, Decimal('0'))

def _earliest_kitchen_activity_date(stock_item):
    dates = []
    first_usage = DailyUsageItem.objects.filter(
        stock_item=stock_item, log__department='kitchen'
    ).order_by('log__date').values_list('log__date', flat=True).first()
    if first_usage:
        dates.append(first_usage)
    first_sale = OrderItem.objects.filter(
        menu_item__recipe__lines__stock_item=stock_item, order__status='confirmed'
    ).order_by('order__created_at').values_list('order__created_at', flat=True).first()
    if first_sale:
        dates.append(first_sale.date())
    return min(dates) if dates else None

def _compute_kitchen_float_day(stock_item, date, opening):
    taken = _taken_amount_for_stock_day(stock_item, date)
    consumed = _consumed_amount_for_stock_day(stock_item, date)
    wasted = _wasted_amount_for_stock_day(stock_item, date)
    closing = opening + taken - consumed - wasted
    return {'opening': opening, 'taken': taken, 'consumed': consumed, 'wasted': wasted, 'closing': closing}

def get_kitchen_float_day(stock_item, date):
    """Returns a dict with opening/taken/consumed/wasted/closing for this ingredient on this day.
    Past days are cached in StockFloatDay (immutable once the day is over); today is always
    computed live, since usage/wastage entries logged today can still change."""
    today = timezone.localdate()

    if date >= today:
        prev = StockFloatDay.objects.filter(stock_item=stock_item, date__lt=date).order_by('-date').first()
        if prev:
            opening = prev.closing
        else:
            earliest = _earliest_kitchen_activity_date(stock_item)
            opening = Decimal('0') if (earliest is None or earliest >= date) else _backfill_and_get_closing(stock_item, date - timedelta(days=1))
        return _compute_kitchen_float_day(stock_item, date, opening)

    existing = StockFloatDay.objects.filter(stock_item=stock_item, date=date).first()
    if existing:
        return {'opening': existing.opening, 'taken': existing.taken, 'consumed': existing.consumed,
                'wasted': existing.wasted, 'closing': existing.closing}

    closing = _backfill_and_get_closing(stock_item, date)
    day = StockFloatDay.objects.get(stock_item=stock_item, date=date)
    return {'opening': day.opening, 'taken': day.taken, 'consumed': day.consumed,
            'wasted': day.wasted, 'closing': day.closing}

def _backfill_and_get_closing(stock_item, up_to_date):
    """Fills in and persists every missing past day up to and including up_to_date, forward
    from the last cached day (or the ingredient's first-ever activity). Returns the closing
    balance on up_to_date."""
    prev = StockFloatDay.objects.filter(stock_item=stock_item, date__lte=up_to_date).order_by('-date').first()
    if prev and prev.date == up_to_date:
        return prev.closing

    if prev:
        opening, cursor = prev.closing, prev.date + timedelta(days=1)
    else:
        earliest = _earliest_kitchen_activity_date(stock_item)
        if earliest is None or earliest > up_to_date:
            return Decimal('0')
        opening, cursor = Decimal('0'), earliest

    closing = opening
    while cursor <= up_to_date:
        result = _compute_kitchen_float_day(stock_item, cursor, opening)
        StockFloatDay.objects.update_or_create(stock_item=stock_item, date=cursor, defaults=result)
        opening = closing = result['closing']
        cursor += timedelta(days=1)
    return closing

def build_kitchen_float_reconciliation(date):
    stock_items = StockItem.objects.filter(department='kitchen', is_active=True)
    rows = []
    for stock in stock_items:
        day = get_kitchen_float_day(stock, date)
        if not any(day[k] for k in ('opening', 'taken', 'consumed', 'wasted')):
            continue
        rows.append({'stock_item': stock, **day})
    rows.sort(key=lambda r: abs(r['closing']), reverse=True)
    return rows

def build_department_day_profit(department, date):
    """Revenue/cost/profit for what a serving department (kitchen or bar) actually SOLD on one day.
    Mirrors the costing logic in the sales PDFs: uses each OrderItem's frozen recipe_cost_snapshot
    where available, falls back to the menu item's manual cost_price, and flags anything neither covers."""
    from .views import _local_day_bounds
    day_start, day_end = _local_day_bounds(date, date)

    if department == 'kitchen':
        item_filter = Q(menu_item__item_type='food') | Q(menu_item__item_type='drink', menu_item__serving_point='kitchen')
    else:
        item_filter = Q(menu_item__item_type='drink', menu_item__serving_point='bar')

    items_qs = OrderItem.objects.filter(
        item_filter, is_cancelled=False, order__status='confirmed',
        order__created_at__gte=day_start, order__created_at__lt=day_end,
    ).select_related('menu_item')

    revenue = Decimal('0')
    cost = Decimal('0')
    any_unreliable = False
    units_sold = 0
    for oi in items_qs:
        revenue += oi.line_total
        units_sold += oi.quantity
        if oi.recipe_cost_snapshot is not None:
            cost += oi.recipe_cost_snapshot
        elif oi.menu_item.cost_price is not None:
            cost += oi.menu_item.cost_price * oi.quantity
            any_unreliable = True
        else:
            any_unreliable = True

    profit = revenue - cost
    return {
        'revenue': revenue, 'cost': cost, 'profit': profit,
        'margin_pct': (profit / revenue * 100) if revenue else None,
        'any_unreliable': any_unreliable, 'units_sold': units_sold,
    }

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