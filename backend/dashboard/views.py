import uuid
import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .permissions import MODULES, get_allowed_modules, has_full_access
from .decorators import staff_module_required

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Sum
from .models import GateLog, Payment
from .forms import GateEntryForm, GateEditForm, ManualBookingForm, PaymentForm
from django.core.paginator import Paginator

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from weasyprint import HTML

from public_site.models import *
from public_site.services import get_available_rooms
from dashboard.models import Payment

#store imports
from store.models import *
from store.forms import DisbursementRequestForm, DisbursementRequestForm, QuickPurchaseForm
from django.db import transaction
from .services import confirm_order_and_deduct_stock

from decimal import Decimal

@login_required
def dashboard_home(request):
    context = {
        'modules': MODULES,
        'allowed_modules': get_allowed_modules(request.user),
    }
    if 'gate' in get_allowed_modules(request.user):
        context['gate_inside_count'] = GateLog.objects.filter(status='inside').count()
    return render(request, 'dashboard/home.html', context)

###start of reception views
@staff_module_required('reception')
def reception_view(request):
    today = timezone.localdate()

    booking_status = request.GET.get('booking_status', '')
    booking_query = request.GET.get('booking_q', '').strip()
    
    # 1. Added prefetch_related for room_service_orders__items__menu_item and assigned_room
    bookings_qs = Booking.objects.select_related('room_type', 'conference_room', 'assigned_room').prefetch_related(
        'room_service_orders__items__menu_item'
    ).order_by('-created_at')
    
    if booking_status:
        bookings_qs = bookings_qs.filter(status=booking_status)
    if booking_query:
        bookings_qs = bookings_qs.filter(Q(guest_name__icontains=booking_query) | Q(guest_phone__icontains=booking_query))
    booking_page_obj = Paginator(bookings_qs, 5).get_page(request.GET.get('booking_page'))

    order_status = request.GET.get('order_status', '')
    
    # 2. Added room_booking__isnull=True to exclude room-service orders
    orders_qs = Order.objects.filter(room_booking__isnull=True).prefetch_related('items__menu_item').order_by('-created_at')
    
    if order_status:
        orders_qs = orders_qs.filter(status=order_status)
    order_page_obj = Paginator(orders_qs, 5).get_page(request.GET.get('order_page'))

    arrivals_today = Booking.objects.filter(check_in=today, status__in=['pending', 'confirmed']).count()
    departures_today = Booking.objects.filter(check_out=today, status='checked_in').count()
    pending_orders_count = Order.objects.filter(status='pending').count()
    today_payments = Payment.objects.filter(created_at__date=today)
    today_revenue = today_payments.aggregate(total=Sum('amount'))['total'] or 0
    
    revenue_by_method = {
        method_code: today_payments.filter(method=method_code).aggregate(total=Sum('amount'))['total'] or 0
        for method_code, _ in Payment.METHOD_CHOICES
    }
    
    # 3. Built data-driven stats structures
    stats = [
        {'label': 'Arrivals Today', 'value': arrivals_today, 'money': False},
        {'label': 'Departures Today', 'value': departures_today, 'money': False},
        {'label': 'Pending Orders', 'value': pending_orders_count, 'money': False},
        {'label': "Today's Revenue", 'value': today_revenue, 'money': True},
    ]
    
    payment_stats = [
        {'label': 'Cash', 'value': revenue_by_method.get('cash', 0)},
        {'label': 'M-Pesa', 'value': revenue_by_method.get('mpesa', 0)},
        {'label': 'Swipe', 'value': revenue_by_method.get('swipe', 0)},
        {'label': 'Equity', 'value': revenue_by_method.get('bank_equity', 0)},
        {'label': 'Family', 'value': revenue_by_method.get('bank_family', 0)},
        {'label': 'Co-op', 'value': revenue_by_method.get('bank_coop', 0)},
    ]
    
    room_service_groups = []
    active_bookings = Booking.objects.filter(status='checked_in').select_related('assigned_room')
    for b in active_bookings:
        pending = b.room_service_orders.filter(status='pending').prefetch_related('items__menu_item')
        if pending.exists():
            room_service_groups.append({
                'booking': b,
                'pending_orders': pending,
                'pending_item_count': sum(o.items.count() for o in pending),
            })

    # 4. Updated context payload
    return render(request, 'dashboard/reception.html', {
        'booking_page_obj': booking_page_obj,
        'order_page_obj': order_page_obj,
        'booking_status': booking_status,
        'booking_query': booking_query,
        'order_status': order_status,
        'stats': stats,
        'payment_stats': payment_stats,
        'room_service_groups': room_service_groups,
    })

@staff_module_required('reception')
def reception_booking_action(request, booking_id, action):
    if request.method != 'POST':
        return redirect('dashboard:reception')
    booking = get_object_or_404(Booking, id=booking_id)

    transitions = {
        'confirm': ('pending', 'confirmed'),
        'check-in': ('confirmed', 'checked_in'),
        'check-out': ('checked_in', 'checked_out'),
        'cancel': (None, 'cancelled'),
    }
    if action not in transitions:
        messages.error(request, "Unknown action.")
        return redirect('dashboard:reception')

    required_status, new_status = transitions[action]
    if action == 'cancel' and booking.status in ('checked_out', 'cancelled'):
        messages.error(request, "This booking can no longer be cancelled.")
        return redirect('dashboard:reception')
    if required_status and booking.status != required_status:
        messages.error(request, f"Cannot {action.replace('-', ' ')} a booking that is currently '{booking.get_status_display()}'.")
        return redirect('dashboard:reception')

    if action == 'check-in':
        if booking.booking_type != 'room':
            messages.error(request, "Only room bookings need a room assignment.")
            return redirect('dashboard:reception')
        available_room = Room.objects.filter(room_type=booking.room_type, status='available').first()
        if not available_room:
            messages.error(request, f"No physical rooms of type '{booking.room_type.name}' are currently marked available. Check the Rooms records in admin.")
            return redirect('dashboard:reception')
        available_room.status = 'occupied'
        available_room.save(update_fields=['status'])
        booking.assigned_room = available_room

        if action == 'check-out':
            if booking.total_balance_due > 0:
                messages.error(request, f"Cannot check out {booking.guest_name}, outstanding balance of KSh {booking.total_balance_due:,.0f} (including room service) must be paid first.")
                return redirect('dashboard:reception')
            if booking.assigned_room:
                booking.assigned_room.status = 'cleaning'
                booking.assigned_room.save(update_fields=['status'])

    booking.status = new_status
    booking.save()
    messages.success(request, f"Booking for {booking.guest_name} marked as {booking.get_status_display()}.")
    return redirect('dashboard:reception')

@staff_module_required('reception')
def reception_order_action(request, order_id, action):
    if request.method != 'POST':
        return redirect('dashboard:reception')
    order = get_object_or_404(Order, id=order_id)

    if action == 'confirm' and order.status == 'pending':
        success, shortfalls = confirm_order_and_deduct_stock(order)
        if not success:
            messages.error(request, f"Cannot confirm, insufficient stock: {', '.join(shortfalls)}.")
            return redirect('dashboard:reception')

    elif action == 'cancel' and order.status in ('pending', 'confirmed'):
        if order.status == 'confirmed':
            with transaction.atomic():
                for order_item in order.items.select_related('menu_item__stock_item'):
                    stock = getattr(order_item.menu_item, 'stock_item', None)
                    if stock:
                        StockItem.objects.filter(id=stock.id).select_for_update().update(
                            quantity_on_hand=stock.quantity_on_hand + order_item.quantity
                        )
        order.status = 'cancelled'
        order.save(update_fields=['status'])
    else:
        messages.error(request, "That action isn't available for this order's current status.")
        return redirect('dashboard:reception')

    messages.success(request, f"Order #{order.id} marked as {order.get_status_display()}.")
    return redirect('dashboard:reception')

@staff_module_required('reception')
def reception_new_booking(request):
    if request.method == 'POST':
        form = ManualBookingForm(request.POST)
        if form.is_valid():
            booking = form.save(commit=False)
            if booking.booking_type == 'room':
                available = get_available_rooms(booking.room_type, booking.check_in, booking.check_out)
                if available < 1:
                    messages.error(request, "No rooms of that type are available for those dates.")
                    return render(request, 'dashboard/reception_new_booking.html', {'form': form})
            booking.save()
            messages.success(request, f"Booking created for {booking.guest_name}.")
            return redirect('dashboard:reception')
    else:
        form = ManualBookingForm(initial={'status': 'confirmed'})
    return render(request, 'dashboard/reception_new_booking.html', {'form': form})


@staff_module_required('reception')
def reception_settle_stay(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    debts = []
    if booking.balance_due > 0:
        debts.append(('booking', booking, booking.balance_due))
    room_orders = booking.room_service_orders.filter(status='confirmed').order_by('created_at')
    for order in room_orders:
        if order.balance_due > 0:
            debts.append(('order', order, order.balance_due))

    total_due = sum(d[2] for d in debts)

    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            method = form.cleaned_data['method']
            reference = form.cleaned_data['reference']
            group_id = uuid.uuid4()
            remaining = amount

            for kind, target, owed in debts:
                if remaining <= 0:
                    break
                pay_now = min(owed, remaining)
                payment = Payment(amount=pay_now, method=method, reference=reference,
                                   received_by=request.user, settlement_group=group_id)
                if kind == 'booking':
                    payment.booking = target
                else:
                    payment.order = target
                payment.save()
                remaining -= pay_now

            messages.success(request, f"Settled KSh {amount - remaining:,.0f} across {booking.guest_name}'s stay.")
            return redirect('dashboard:reception')
    else:
        form = PaymentForm(initial={'amount': total_due})

    return render(request, 'dashboard/reception_settle_stay.html', {
        'form': form, 'booking': booking, 'debts': debts, 'total_due': total_due,
    })


@staff_module_required('reception')
def reception_confirm_all_orders(request, booking_id):
    if request.method != 'POST':
        return redirect('dashboard:reception')
    booking = get_object_or_404(Booking, id=booking_id)
    pending_orders = booking.room_service_orders.filter(status='pending')

    confirmed_count, shortfall_items = 0, []
    for order in pending_orders:
        success, shortfalls = confirm_order_and_deduct_stock(order)
        if success:
            confirmed_count += 1
        else:
            shortfall_items.extend(shortfalls)

    if confirmed_count:
        messages.success(request, f"Confirmed {confirmed_count} order(s) for {booking.guest_name}.")
    if shortfall_items:
        messages.error(request, f"Could not confirm, insufficient stock: {', '.join(set(shortfall_items))}.")
    return redirect('dashboard:reception')


@staff_module_required('reception')
def reception_record_payment(request, target_type, target_id):
    if target_type not in ('booking', 'order'):
        messages.error(request, "Invalid payment target.")
        return redirect('dashboard:reception')

    target = get_object_or_404(Booking if target_type == 'booking' else Order, id=target_id)

    if request.method == 'POST':
        instance = Payment(booking=target) if target_type == 'booking' else Payment(order=target)
        form = PaymentForm(request.POST, instance=instance)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.received_by = request.user
            payment.save()
            messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded.")
            return redirect('dashboard:reception')
    else:
        form = PaymentForm()

    return render(request, 'dashboard/reception_payment.html', {
        'form': form, 'target': target, 'target_type': target_type,
    })

##end of reception views

@staff_module_required('rooms')
def rooms_view(request):
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Rooms'})


@staff_module_required('restaurant_kitchen')
def restaurant_tables_view(request):
    tables = Table.objects.all().order_by('number')
    today = timezone.localdate()

    restaurant_payments = Payment.objects.filter(order__room_booking__isnull=True, created_at__date=today)
    today_revenue = restaurant_payments.aggregate(total=Sum('amount'))['total'] or 0
    revenue_by_method = {
        code: restaurant_payments.filter(method=code).aggregate(total=Sum('amount'))['total'] or 0
        for code, _ in Payment.METHOD_CHOICES
    }

    offsite_qs = Order.objects.filter(room_booking__isnull=True, table__isnull=True, status='confirmed').prefetch_related('items__menu_item').order_by('-created_at')
    offsite_open_orders = [o for o in offsite_qs if o.balance_due > 0]

    return render(request, 'dashboard/restaurant_tables.html', {
        'tables': tables,
        'today_revenue': today_revenue,
        'revenue_by_method': revenue_by_method,
        'offsite_open_orders': offsite_open_orders,
    })

@staff_module_required('restaurant_kitchen')
def restaurant_table_pos(request, table_id):
    table = get_object_or_404(Table, id=table_id)
    open_orders = table.open_orders

    selected_order = None
    order_param = request.GET.get('order')
    if order_param and order_param != 'new':
        selected_order = next((o for o in open_orders if str(o.id) == order_param), None)

    food_items = MenuItem.objects.filter(item_type='food', is_available=True).select_related('stock_item').prefetch_related('images')
    kitchen_drinks = MenuItem.objects.filter(item_type='drink', serving_point='kitchen', is_available=True).select_related('stock_item').prefetch_related('images')
    menu_items = [i for i in list(food_items) + list(kitchen_drinks) if i.is_in_stock]
    categories = sorted(set(i.category for i in menu_items if i.category))

    return render(request, 'dashboard/restaurant_table_pos.html', {
        'table': table,
        'open_orders': open_orders,
        'selected_order': selected_order,
        'menu_items': menu_items,
        'categories': categories,
        'default_tier': 'vip' if table.is_vip else 'regular',
    })

@staff_module_required('restaurant_kitchen')
def restaurant_add_items(request, order_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen')
    order = get_object_or_404(Order, id=order_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    raw_items = data.get('items', [])
    if not raw_items:
        return JsonResponse({'error': 'Add at least one item.'}, status=400)

    resolved = []
    for item in raw_items:
        try:
            menu_item = MenuItem.objects.select_related('stock_item').get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        tier = item.get('tier') if item.get('tier') in ('regular', 'vip') else 'regular'
        resolved.append((menu_item, tier, max(int(item.get('qty', 1)), 1)))

    shortfalls = [mi.name for mi, _, qty in resolved if getattr(mi, 'stock_item', None) and mi.stock_item.quantity_on_hand < qty]
    if shortfalls:
        return JsonResponse({'error': f"Insufficient stock: {', '.join(shortfalls)}."}, status=409)

    with transaction.atomic():
        for menu_item, tier, qty in resolved:
            OrderItem.objects.create(
                order=order, menu_item=menu_item, tier=tier, quantity=qty,
                prep_status='ready' if menu_item.is_quick_serve else 'queued',
            )
            stock = getattr(menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand - qty)

    return JsonResponse({'success': True})

@staff_module_required('restaurant_kitchen')
def restaurant_new_order(request, table_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen')
    table = get_object_or_404(Table, id=table_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'Add at least one item.'}, status=400)

    order = Order.objects.create(
        customer_name=data.get('customer_name', '').strip() or f"Table {table.number}",
        customer_phone=data.get('customer_phone', '').strip(),
        notes=data.get('notes', '').strip(),
        table=table,
        status='pending',
    )
    for item in items:
        try:
            menu_item = MenuItem.objects.get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        tier = item.get('tier') if item.get('tier') in ('regular', 'vip') else 'regular'
        OrderItem.objects.create(order=order, menu_item=menu_item, tier=tier, quantity=max(int(item.get('qty', 1)), 1))

    success, shortfalls = confirm_order_and_deduct_stock(order)
    if not success:
        order.delete()
        return JsonResponse({'error': f"Insufficient stock: {', '.join(shortfalls)}."}, status=409)

    
    return JsonResponse({'success': True})


COURSE_PRIORITY = {'starter': 0, 'main': 1, 'dessert': 2}


@staff_module_required('restaurant_kitchen')
def restaurant_kitchen_display(request):

    active_items = OrderItem.objects.filter(
        prep_status__in=['queued', 'preparing', 'ready'],
        order__status='confirmed',
    ).filter(
        Q(menu_item__item_type='food') | Q(menu_item__item_type='drink', menu_item__serving_point='kitchen')
    ).select_related('menu_item', 'order', 'order__table').order_by('order__created_at')
    
    active_items = sorted(
        active_items,
        key=lambda oi: (COURSE_PRIORITY.get(oi.menu_item.course, 1), oi.order.created_at)
    )

    return render(request, 'dashboard/restaurant_kitchen_display.html', {'active_items': active_items})


@staff_module_required('restaurant_kitchen')
def restaurant_advance_item(request, item_id, new_status):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen_display')
    order_item = get_object_or_404(OrderItem, id=item_id)
    valid_next = {'queued': 'preparing', 'preparing': 'ready', 'ready': 'served'}
    if valid_next.get(order_item.prep_status) != new_status:
        messages.error(request, "That status change isn't valid from the item's current state.")
        return redirect('dashboard:restaurant_kitchen_display')
    order_item.prep_status = new_status
    order_item.save(update_fields=['prep_status'])
    return redirect('dashboard:restaurant_kitchen_display')

VAT_RATE = Decimal('0.16')  # menu prices are treated as VAT-inclusive


@staff_module_required('restaurant_kitchen')
def restaurant_record_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id, room_booking__isnull=True)

    if request.method == 'POST':
        instance = Payment(order=order)
        form = PaymentForm(request.POST, instance=instance)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.received_by = request.user
            payment.save()
            messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded for Order #{order.id}.")
            return redirect('dashboard:restaurant_kitchen')
    else:
        form = PaymentForm(initial={'amount': order.balance_due})

    return render(request, 'dashboard/restaurant_payment.html', {'form': form, 'order': order})


@staff_module_required('restaurant_kitchen')
def restaurant_new_offsite_order(request):
    food_items = MenuItem.objects.filter(item_type='food', is_available=True).select_related('stock_item').prefetch_related('images')
    kitchen_drinks = MenuItem.objects.filter(item_type='drink', serving_point='kitchen', is_available=True).select_related('stock_item').prefetch_related('images')
    menu_items = [i for i in list(food_items) + list(kitchen_drinks) if i.is_in_stock]
    categories = sorted(set(i.category for i in menu_items if i.category))
    return render(request, 'dashboard/restaurant_offsite_order.html', {'menu_items': menu_items, 'categories': categories})


@staff_module_required('restaurant_kitchen')
def restaurant_create_offsite_order(request):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen')
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    order_type = data.get('order_type')
    if order_type not in ('takeaway', 'conference', 'event'):
        return JsonResponse({'error': 'Invalid order type.'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'Add at least one item.'}, status=400)

    order = Order.objects.create(
        customer_name=data.get('customer_name', '').strip() or order_type.replace('_', ' ').title(),
        customer_phone=data.get('customer_phone', '').strip(),
        notes=data.get('notes', '').strip(),
        order_type=order_type,
        served_at=data.get('served_at', '').strip(),
        party_size=data.get('party_size') or None,
        status='pending',
    )
    for item in items:
        try:
            menu_item = MenuItem.objects.get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        tier = item.get('tier') if item.get('tier') in ('regular', 'vip') else 'regular'
        OrderItem.objects.create(order=order, menu_item=menu_item, tier=tier, quantity=max(int(item.get('qty', 1)), 1))

    success, shortfalls = confirm_order_and_deduct_stock(order)
    if not success:
        order.delete()
        return JsonResponse({'error': f"Insufficient stock: {', '.join(shortfalls)}."}, status=409)

    return JsonResponse({'success': True})


@staff_module_required('restaurant_kitchen')
def restaurant_export_pdf(request):
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    orders_qs = Order.objects.filter(room_booking__isnull=True, status='confirmed').prefetch_related('items__menu_item').order_by('created_at')
    if date_from:
        orders_qs = orders_qs.filter(created_at__date__gte=date_from)
    if date_to:
        orders_qs = orders_qs.filter(created_at__date__lte=date_to)

    grand_total = Decimal('0')
    for order in orders_qs:
        grand_total += order.total_amount

    subtotal_excl_vat = grand_total / (1 + VAT_RATE)
    vat_amount = grand_total - subtotal_excl_vat

    html_string = render_to_string('dashboard/restaurant_report_pdf.html', {
        'orders': orders_qs,
        'date_from': date_from,
        'date_to': date_to,
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': vat_amount,
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="restaurant_sales_{timezone.now().date()}.pdf"'
    return response


@staff_module_required('bar')
def bar_view(request):
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Bar'})


@staff_module_required('gate')
def gate_view(request):
    if request.method == 'POST':
        form = GateEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.logged_in_by = request.user
            entry.status = 'inside'
            entry.save()
            messages.success(request, f"Entry logged for {entry.guest_name} ({entry.plate_number}).")
            return redirect('dashboard:gate')
    else:
        form = GateEntryForm()

    # ---------- Currently inside (paginated, no filters) ----------
    inside_qs = GateLog.objects.filter(status='inside').select_related('logged_in_by')
    inside_paginator = Paginator(inside_qs, 10)
    inside_page_obj = inside_paginator.get_page(request.GET.get('inside_page'))

    # ---------- History (filterable + paginated) ----------
    query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    history_qs = GateLog.objects.filter(status='exited').select_related('logged_in_by', 'logged_out_by')

    if query:
        history_qs = history_qs.filter(Q(guest_name__icontains=query) | Q(plate_number__icontains=query))
    if date_from:
        history_qs = history_qs.filter(entry_time__date__gte=date_from)
    if date_to:
        history_qs = history_qs.filter(entry_time__date__lte=date_to)

    history_paginator = Paginator(history_qs, 15)
    history_page_obj = history_paginator.get_page(request.GET.get('history_page'))
    current_user_full_access = has_full_access(request.user)

    today = timezone.localdate()
    today_entries = GateLog.objects.filter(entry_time__date=today).count()
    today_exits = GateLog.objects.filter(exit_time__date=today).count()

    return render(request, 'dashboard/gate.html', {
        'form': form,
        'inside_page_obj': inside_page_obj,
        'history_page_obj': history_page_obj,
        'query': query,
        'date_from': date_from,
        'date_to': date_to,
        'currently_inside_count': inside_qs.count(),
        'today_entries': today_entries,
        'today_exits': today_exits,
        'current_user_full_access': current_user_full_access,
    })

@staff_module_required('gate')
def gate_log_exit(request, log_id):
    if request.method != 'POST':
        return redirect('dashboard:gate')
    log = get_object_or_404(GateLog, id=log_id, status='inside')
    log.status = 'exited'
    log.exit_time = timezone.now()
    log.logged_out_by = request.user
    log.save()
    messages.success(request, f"Exit logged for {log.guest_name} ({log.plate_number}).")
    return redirect('dashboard:gate')

@staff_module_required('gate')
def gate_edit_entry(request, log_id):
    log = get_object_or_404(GateLog, id=log_id)

    can_edit = (log.logged_in_by_id == request.user.id) or has_full_access(request.user)
    if not can_edit:
        messages.error(request, "You can only edit entries you logged yourself, unless you're a manager.")
        return redirect('dashboard:gate')

    if request.method == 'POST':
        form = GateEditForm(request.POST, instance=log)
        if form.is_valid():
            form.save()
            messages.success(request, f"Entry for {log.guest_name} updated.")
            return redirect('dashboard:gate')
    else:
        form = GateEditForm(instance=log)

    return render(request, 'dashboard/gate_edit.html', {'form': form, 'log': log})

@staff_module_required('gate')
def gate_export_pdf(request):
    query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    history_qs = GateLog.objects.filter(status='exited').select_related('logged_in_by', 'logged_out_by')
    if query:
        history_qs = history_qs.filter(Q(guest_name__icontains=query) | Q(plate_number__icontains=query))
    if date_from:
        history_qs = history_qs.filter(entry_time__date__gte=date_from)
    if date_to:
        history_qs = history_qs.filter(entry_time__date__lte=date_to)

    html_string = render_to_string('dashboard/gate_report_pdf.html', {
        'logs': history_qs,
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'query': query,
        'date_from': date_from,
        'date_to': date_to,
        'total_count': history_qs.count(),
    })

    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="gate_report_{timezone.now().date()}.pdf"'
    return response


@staff_module_required('hrm')
def hrm_view(request):
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'HRM & Management'})


@staff_module_required('store')
def store_view(request):
    department_filter = request.GET.get('department', '')
    status_filter = request.GET.get('status', '')

    disbursements_qs = Disbursement.objects.select_related('requested_by', 'approved_by', 'disbursed_by').order_by('-created_at')
    if department_filter:
        disbursements_qs = disbursements_qs.filter(department=department_filter)
    if status_filter:
        disbursements_qs = disbursements_qs.filter(status=status_filter)
    disbursement_page_obj = Paginator(disbursements_qs, 10).get_page(request.GET.get('d_page'))

    low_stock_items = [item for item in StockItem.objects.filter(is_active=True) if item.is_low_stock]

    if request.method == 'POST':
        form = DisbursementRequestForm(request.POST)
        if form.is_valid():
            Disbursement.objects.create(
                department=form.cleaned_data['department'],
                purpose=form.cleaned_data['purpose'],
                amount=form.cleaned_data['amount'],
                requested_by=request.user,
            )
            messages.success(request, "Disbursement request submitted.")
            return redirect('dashboard:store')
    else:
        form = DisbursementRequestForm()

    return render(request, 'dashboard/store.html', {
        'form': form,
        'disbursement_page_obj': disbursement_page_obj,
        'department_filter': department_filter,
        'status_filter': status_filter,
        'low_stock_items': low_stock_items,
        'can_approve': has_full_access(request.user),
    })


@staff_module_required('store')
def store_disbursement_approve(request, disbursement_id):
    if request.method != 'POST' or not has_full_access(request.user):
        messages.error(request, "Only managers can approve disbursement requests.")
        return redirect('dashboard:store')
    d = get_object_or_404(Disbursement, id=disbursement_id, status='requested')
    d.status = 'approved'
    d.approved_by = request.user
    d.approved_at = timezone.now()
    d.save()
    messages.success(request, f"Disbursement for {d.get_department_display()} approved.")
    return redirect('dashboard:store')


@staff_module_required('store')
def store_disbursement_reject(request, disbursement_id):
    if request.method != 'POST' or not has_full_access(request.user):
        messages.error(request, "Only managers can reject disbursement requests.")
        return redirect('dashboard:store')
    d = get_object_or_404(Disbursement, id=disbursement_id, status='requested')
    d.status = 'rejected'
    d.approved_by = request.user
    d.approved_at = timezone.now()
    d.save()
    messages.success(request, "Disbursement request rejected.")
    return redirect('dashboard:store')


@staff_module_required('store')
def store_disbursement_disburse(request, disbursement_id):
    if request.method != 'POST':
        return redirect('dashboard:store')
    d = get_object_or_404(Disbursement, id=disbursement_id, status='approved')
    d.status = 'disbursed'
    d.disbursed_by = request.user
    d.disbursed_at = timezone.now()
    d.save()
    messages.success(request, f"KSh {d.amount:,.0f} marked as disbursed to {d.get_department_display()}.")
    return redirect('dashboard:store')


@staff_module_required('store')
def store_disbursement_detail(request, disbursement_id):
    d = get_object_or_404(Disbursement, id=disbursement_id)

    if request.method == 'POST':
        if d.status != 'disbursed':
            messages.error(request, "Purchases can only be recorded against a disbursed fund.")
            return redirect('dashboard:store_disbursement_detail', disbursement_id=d.id)

        form = QuickPurchaseForm(request.POST, department=d.department)
        if form.is_valid():
            data = form.cleaned_data

            if data['mode'] == 'existing':
                stock_item = data['stock_item']
            else:
                menu_item = None
                if data['add_to_menu']:
                    menu_item = MenuItem.objects.create(
                        name=data['new_item_name'],
                        item_type='drink' if d.department == 'bar' else 'food',
                        serving_point='bar' if d.department == 'bar' else 'kitchen',
                        category=data.get('menu_category', ''),
                        regular_price=data['menu_regular_price'],
                        vip_price=data.get('menu_vip_price') or None,
                        description=data.get('menu_description', ''),
                        is_available=False,
                    )
                stock_item = StockItem.objects.create(
                    name=data['new_item_name'],
                    department=d.department,
                    unit=data['new_item_unit'],
                    reorder_level=data.get('reorder_level') or 0,
                    linked_menu_item=menu_item,
                )

            DisbursementPurchase.objects.create(
                disbursement=d,
                stock_item=stock_item,
                quantity=data['quantity'],
                unit_cost=data['unit_cost'],
                receipt_reference=data.get('receipt_reference', ''),
                recorded_by=request.user,
            )

            msg = "Purchase recorded and stock updated."
            if data['mode'] == 'new' and data.get('add_to_menu'):
                msg += " New menu item saved as a draft, add photos and publish it from the admin panel."
            messages.success(request, msg)
            return redirect('dashboard:store_disbursement_detail', disbursement_id=d.id)
    else:
        form = QuickPurchaseForm(department=d.department)

    return render(request, 'dashboard/store_disbursement_detail.html', {
        'disbursement': d,
        'form': form,
        'purchases': d.purchases.select_related('stock_item', 'recorded_by'),
        'is_menu_department': d.department in ('kitchen', 'bar'),
    })


@staff_module_required('store')
def store_disbursement_reconcile(request, disbursement_id):
    if request.method != 'POST':
        return redirect('dashboard:store')
    d = get_object_or_404(Disbursement, id=disbursement_id, status='disbursed')
    d.status = 'reconciled'
    d.reconciled_at = timezone.now()
    d.save()
    messages.success(request, f"Reconciled. Variance: KSh {d.variance:,.0f}.")
    return redirect('dashboard:store_disbursement_detail', disbursement_id=d.id)