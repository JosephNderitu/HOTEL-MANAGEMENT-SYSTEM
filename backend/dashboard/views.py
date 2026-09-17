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
from .forms import *
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
from .services import *

from decimal import Decimal
from django.urls import reverse

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
    return _settle_stay_view(request, booking_id, back_url_name='dashboard:reception')

def _settle_stay_view(request, booking_id, back_url_name):
    booking = get_object_or_404(Booking, id=booking_id)
    debts = []
    if booking.balance_due > 0:
        debts.append(('booking', booking, booking.balance_due))
    for order in booking.room_service_orders.filter(status='confirmed').order_by('created_at'):
        if order.balance_due > 0:
            debts.append(('order', order, order.balance_due))
    total_due = sum(d[2] for d in debts)

    if request.method == 'POST':
        form = SettlementForm(request.POST)
        if form.is_valid():
            settled, change = settle_booking_stay(
                booking, form.cleaned_data['amount'], form.cleaned_data.get('amount_tendered'),
                form.cleaned_data['method'], form.cleaned_data.get('reference', ''), request.user,
            )
            if change > 0:
                messages.success(request, f"Settled KSh {settled:,.0f}. CHANGE DUE: KSh {change:,.0f}")
            else:
                messages.success(request, f"Settled KSh {settled:,.0f} across {booking.guest_name}'s stay.")
            return redirect(back_url_name)
    else:
        form = SettlementForm(initial={'amount': total_due})

    return render(request, 'dashboard/settle_stay.html', {
        'form': form, 'booking': booking, 'debts': debts, 'total_due': total_due, 'back_url_name': back_url_name,
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
            if payment.change_given > 0:
                messages.success(request, f"Payment recorded. CHANGE DUE: KSh {payment.change_given:,.0f}")
            else:
                messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded.")
            return redirect('dashboard:reception')
    else:
        form = PaymentForm()

    return render(request, 'dashboard/reception_payment.html', {
        'form': form, 'target': target, 'target_type': target_type,
    })

##end of reception views

### start of room view ####
@staff_module_required('rooms')
def rooms_view(request):
    status_filter = request.GET.get('status', '')
    rooms = Room.objects.select_related('room_type').order_by('room_type__name', 'number')
    if status_filter:
        rooms = rooms.filter(status=status_filter)

    counts = {
        'available': Room.objects.filter(status='available').count(),
        'occupied': Room.objects.filter(status='occupied').count(),
        'cleaning': Room.objects.filter(status='cleaning').count(),
        'maintenance': Room.objects.filter(status='maintenance').count(),
    }

    open_maintenance_count = MaintenanceRequest.objects.filter(status__in=['open', 'in_progress']).count()
    unclaimed_lost_count = LostFoundItem.objects.filter(status='unclaimed').count()

    active_bookings = {
        b.assigned_room_id: b
        for b in Booking.objects.filter(status='checked_in', assigned_room__isnull=False)
    }

    return render(request, 'dashboard/rooms.html', {
        'rooms': rooms,
        'counts': counts,
        'status_filter': status_filter,
        'open_maintenance_count': open_maintenance_count,
        'unclaimed_lost_count': unclaimed_lost_count,
        'active_bookings': active_bookings,
    })


@staff_module_required('rooms')
def room_start_cleaning(request, room_id):
    if request.method != 'POST':
        return redirect('dashboard:rooms')
    room = get_object_or_404(Room, id=room_id, status='cleaning')

    log = room.cleaning_logs.filter(completed_at__isnull=True).first()
    if not log:
        log = RoomCleaningLog.objects.create(room=room, started_by=request.user)
    return redirect('dashboard:room_cleaning_checklist', room_id=room.id)


@staff_module_required('rooms')
def room_cleaning_checklist(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    log = room.cleaning_logs.filter(completed_at__isnull=True).order_by('-started_at').first()
    if not log:
        messages.error(request, "No active cleaning session for this room. Start one from the room board.")
        return redirect('dashboard:rooms')

    if request.method == 'POST':
        checklist_data = {key: (request.POST.get(key) == 'on') for key, _ in CLEANING_CHECKLIST_ITEMS}
        log.checklist_data = checklist_data
        log.save(update_fields=['checklist_data'])

        if all(checklist_data.values()):
            log.completed_by = request.user
            log.completed_at = timezone.now()
            log.save(update_fields=['completed_by', 'completed_at'])
            room.status = 'available'
            room.save(update_fields=['status'])
            messages.success(request, f"Room {room.number} marked Available. Checklist complete.")
            return redirect('dashboard:rooms')
        else:
            missing = [label for key, label in CLEANING_CHECKLIST_ITEMS if not checklist_data.get(key)]
            messages.error(request, f"All items must be checked before this room can go Available. Still missing: {', '.join(missing)}.")

    return render(request, 'dashboard/room_cleaning_checklist.html', {
        'room': room, 'log': log, 'checklist_items': CLEANING_CHECKLIST_ITEMS,
    })


@staff_module_required('rooms')
def room_report_issue(request, room_id):
    if request.method != 'POST':
        return redirect('dashboard:rooms')
    room = get_object_or_404(Room, id=room_id)

    issue = request.POST.get('issue', '').strip()
    if not issue:
        messages.error(request, "Describe the issue before submitting.")
        return redirect('dashboard:rooms')

    MaintenanceRequest.objects.create(
        room=room,
        issue=issue,
        description=request.POST.get('description', '').strip(),
        priority=request.POST.get('priority', 'medium'),
        reported_by=request.user,
    )
    room.status = 'maintenance'
    room.save(update_fields=['status'])
    messages.success(request, f"Issue reported for Room {room.number}. Room marked under Maintenance.")
    return redirect('dashboard:rooms')


@staff_module_required('rooms')
def maintenance_list(request):
    status_filter = request.GET.get('status', '')
    requests_qs = MaintenanceRequest.objects.select_related('room', 'reported_by', 'resolved_by').order_by('-created_at')
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)
    return render(request, 'dashboard/maintenance_list.html', {'requests': requests_qs, 'status_filter': status_filter})


@staff_module_required('rooms')
def maintenance_start(request, request_id):
    if request.method != 'POST':
        return redirect('dashboard:maintenance_list')
    req = get_object_or_404(MaintenanceRequest, id=request_id, status='open')
    req.status = 'in_progress'
    req.save(update_fields=['status'])
    messages.success(request, "Marked in progress.")
    return redirect('dashboard:maintenance_list')


@staff_module_required('rooms')
def maintenance_resolve(request, request_id):
    if request.method != 'POST':
        return redirect('dashboard:maintenance_list')
    req = get_object_or_404(MaintenanceRequest, id=request_id)
    req.status = 'resolved'
    req.resolved_by = request.user
    req.resolved_at = timezone.now()
    req.save(update_fields=['status', 'resolved_by', 'resolved_at'])

    # Fixed rooms go through Cleaning before reuse, repair work disturbs the room.
    req.room.status = 'cleaning'
    req.room.save(update_fields=['status'])
    messages.success(request, f"Resolved. Room {req.room.number} moved to Cleaning before it's marked Available.")
    return redirect('dashboard:maintenance_list')


@staff_module_required('rooms')
def lostfound_list(request):
    status_filter = request.GET.get('status', 'unclaimed')
    items = LostFoundItem.objects.select_related('room', 'found_by').order_by('-found_at')
    if status_filter:
        items = items.filter(status=status_filter)
    rooms = Room.objects.all().order_by('number')
    return render(request, 'dashboard/lostfound_list.html', {'items': items, 'status_filter': status_filter, 'rooms': rooms})


@staff_module_required('rooms')
def lostfound_add(request):
    if request.method != 'POST':
        return redirect('dashboard:lostfound_list')
    description = request.POST.get('description', '').strip()
    if not description:
        messages.error(request, "Describe the item before submitting.")
        return redirect('dashboard:lostfound_list')

    room_id = request.POST.get('room') or None
    LostFoundItem.objects.create(
        description=description,
        room_id=room_id,
        found_location=request.POST.get('found_location', '').strip(),
        found_by=request.user,
    )
    messages.success(request, "Item logged.")
    return redirect('dashboard:lostfound_list')


@staff_module_required('rooms')
def lostfound_claim(request, item_id):
    if request.method != 'POST':
        return redirect('dashboard:lostfound_list')
    item = get_object_or_404(LostFoundItem, id=item_id, status='unclaimed')
    claimant = request.POST.get('claimed_by_name', '').strip()
    if not claimant:
        messages.error(request, "Enter who claimed the item.")
        return redirect('dashboard:lostfound_list')
    item.status = 'claimed'
    item.claimed_by_name = claimant
    item.claimed_at = timezone.now()
    item.save(update_fields=['status', 'claimed_by_name', 'claimed_at'])
    messages.success(request, "Marked claimed.")
    return redirect('dashboard:lostfound_list')


@staff_module_required('rooms')
def lostfound_dispose(request, item_id):
    if request.method != 'POST':
        return redirect('dashboard:lostfound_list')
    item = get_object_or_404(LostFoundItem, id=item_id, status='unclaimed')
    item.status = 'disposed'
    item.save(update_fields=['status'])
    messages.success(request, "Marked disposed.")
    return redirect('dashboard:lostfound_list')

#### end of room views ####

### start of restaurant views
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
    waiting_count = WaitlistEntry.objects.filter(status='waiting').count()

    return render(request, 'dashboard/restaurant_tables.html', {
        'tables': tables,
        'today_revenue': today_revenue,
        'revenue_by_method': revenue_by_method,
        'offsite_open_orders': offsite_open_orders,
        'waiting_count': waiting_count,
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

from django.db.models import Count, Sum as DjangoSum
from store.models import DailyUsageLog, DailyUsageItem

@staff_module_required('restaurant_kitchen')
def restaurant_kitchen_display(request):
    today = timezone.localdate()

    todays_orders = Order.objects.filter(status='confirmed', created_at__date=today).prefetch_related('items__menu_item')
    kitchen_orders_today = [o for o in todays_orders if o.has_kitchen_items]

    total_orders = len(kitchen_orders_today)
    served_count = sum(1 for o in kitchen_orders_today if o.kitchen_fully_served)
    pending_count = sum(1 for o in kitchen_orders_today if all(i.prep_status == 'queued' for i in o.kitchen_items()))
    in_progress_count = total_orders - served_count - pending_count

    active_orders = [
        o for o in Order.objects.filter(status='confirmed').prefetch_related('items__menu_item', 'table')
        if o.has_kitchen_items and not o.kitchen_fully_served
    ]
    active_orders.sort(key=lambda o: o.created_at)

    closed_orders = [o for o in kitchen_orders_today if o.kitchen_fully_served]
    closed_orders.sort(key=lambda o: o.created_at, reverse=True)

    kitchen_log, _ = DailyUsageLog.objects.get_or_create(department='kitchen', date=today)

    return render(request, 'dashboard/restaurant_kitchen_display.html', {
        'stats': {
            'total': total_orders, 'served': served_count,
            'pending': pending_count, 'in_progress': in_progress_count,
        },
        'active_orders': active_orders,
        'closed_orders': closed_orders,
        'kitchen_log': kitchen_log,
    })


@staff_module_required('restaurant_kitchen')
def kitchen_item_sales_pdf(request):
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    items_qs = OrderItem.objects.filter(is_cancelled=False, order__status='confirmed').filter(
        Q(menu_item__item_type='food') | Q(menu_item__item_type='drink', menu_item__serving_point='kitchen')
    )
    if date_from:
        items_qs = items_qs.filter(order__created_at__date__gte=date_from)
    if date_to:
        items_qs = items_qs.filter(order__created_at__date__lte=date_to)

    sold = {}
    for oi in items_qs.select_related('menu_item'):
        m = oi.menu_item
        sold.setdefault(m.id, {'item': m, 'qty': 0, 'amount': Decimal('0')})
        sold[m.id]['qty'] += oi.quantity
        sold[m.id]['amount'] += oi.line_total

    all_kitchen_items = MenuItem.objects.filter(
        Q(item_type='food') | Q(item_type='drink', serving_point='kitchen')
    ).order_by('name')

    rows = []
    for m in all_kitchen_items:
        entry = sold.get(m.id)
        qty = entry['qty'] if entry else 0
        amount = entry['amount'] if entry else Decimal('0')
        rows.append({'name': m.name, 'category': m.category, 'qty': qty, 'unit_price': m.regular_price, 'amount': amount})

    rows.sort(key=lambda r: r['qty'], reverse=True)

    grand_total = sum((r['amount'] for r in rows), Decimal('0'))
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')
    vat_amount = grand_total - subtotal_excl_vat

    html_string = render_to_string('dashboard/kitchen_sales_pdf.html', {
        'rows': rows, 'date_from': date_from, 'date_to': date_to,
        'generated_at': timezone.now(), 'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total, 'subtotal_excl_vat': subtotal_excl_vat, 'vat_amount': vat_amount,
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="kitchen_item_sales_{timezone.now().date()}.pdf"'
    return response


def _can_touch_usage_log(user):
    return can_access(user, 'restaurant_kitchen') or can_access(user, 'store')


@login_required
def kitchen_usage_log_view(request):
    if not _can_touch_usage_log(request.user):
        return render(request, 'dashboard/restricted.html', {'module_label': 'Kitchen Usage Log'}, status=403)

    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department='kitchen', date=today)
    stock_items = StockItem.objects.filter(department='kitchen', is_active=True)

    return render(request, 'dashboard/kitchen_usage_log.html', {
        'log': log, 'stock_items': stock_items,
        'items': log.items.select_related('stock_item', 'added_by').order_by('-added_at'),
    })


@login_required
def kitchen_usage_log_add(request):
    if request.method != 'POST' or not _can_touch_usage_log(request.user):
        return redirect('dashboard:kitchen_usage_log')

    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department='kitchen', date=today)
    if log.status != 'draft':
        messages.error(request, "Today's log is already confirmed and locked.")
        return redirect('dashboard:kitchen_usage_log')

    mode = request.POST.get('mode')
    quantity = Decimal(request.POST.get('quantity') or '0')
    if quantity <= 0:
        messages.error(request, "Enter a quantity greater than zero.")
        return redirect('dashboard:kitchen_usage_log')

    with transaction.atomic():
        if mode == 'stock':
            stock = get_object_or_404(StockItem, id=request.POST.get('stock_item'), department='kitchen')
            if quantity > stock.quantity_on_hand:
                messages.error(request, f"Can't log more than what's in stock ({stock.quantity_on_hand} {stock.unit}).")
                return redirect('dashboard:kitchen_usage_log')
            DailyUsageItem.objects.create(log=log, stock_item=stock, quantity=quantity, added_by=request.user)
            StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand - quantity)
        else:
            name = request.POST.get('custom_name', '').strip()
            unit = request.POST.get('custom_unit', '')
            if not name:
                messages.error(request, "Enter a name for the item.")
                return redirect('dashboard:kitchen_usage_log')
            DailyUsageItem.objects.create(log=log, custom_name=name, custom_unit=unit, quantity=quantity, added_by=request.user)

    messages.success(request, "Usage logged.")
    return redirect('dashboard:kitchen_usage_log')


@login_required
def kitchen_usage_log_delete(request, item_id):
    if request.method != 'POST' or not _can_touch_usage_log(request.user):
        return redirect('dashboard:kitchen_usage_log')
    item = get_object_or_404(DailyUsageItem, id=item_id)
    if item.log.status != 'draft':
        messages.error(request, "This log is confirmed and locked.")
        return redirect('dashboard:kitchen_usage_log')

    with transaction.atomic():
        if item.stock_item:
            StockItem.objects.filter(id=item.stock_item.id).select_for_update().update(
                quantity_on_hand=item.stock_item.quantity_on_hand + item.quantity
            )
        item.delete()
    messages.success(request, "Entry removed.")
    return redirect('dashboard:kitchen_usage_log')


@login_required
def kitchen_usage_log_confirm(request):
    if request.method != 'POST' or not _can_touch_usage_log(request.user):
        return redirect('dashboard:kitchen_usage_log')
    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department='kitchen', date=today)
    if not log.items.exists():
        messages.error(request, "Add at least one item before confirming.")
        return redirect('dashboard:kitchen_usage_log')
    log.status = 'confirmed'
    log.confirmed_by = request.user
    log.confirmed_at = timezone.now()
    log.save(update_fields=['status', 'confirmed_by', 'confirmed_at'])
    messages.success(request, "Today's usage log confirmed and locked.")
    return redirect('dashboard:kitchen_usage_log')

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

    if not order.is_fully_served:
        messages.error(request, "This order can't be paid yet, every item must be marked Served first.")
        return redirect('dashboard:restaurant_kitchen')
    
    if request.method == 'POST':
        instance = Payment(order=order)
        form = PaymentForm(request.POST, instance=instance)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.received_by = request.user
            payment.save()
            if payment.change_given > 0:
                messages.success(request, f"Payment recorded. CHANGE DUE: KSh {payment.change_given:,.0f}")
            else:
                messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded.")
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
def restaurant_cancel_item(request, item_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen')
    order_item = get_object_or_404(OrderItem, id=item_id)
    reason = request.POST.get('reason', '').strip()

    if not reason:
        messages.error(request, "A reason is required to cancel an item.")
    elif order_item.prep_status not in ('queued', 'preparing'):
        messages.error(request, f"Can't cancel {order_item.menu_item.name}, it's already {order_item.get_prep_status_display().lower()}.")
    else:
        with transaction.atomic():
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(
                    quantity_on_hand=stock.quantity_on_hand + order_item.quantity
                )
            order_item.is_cancelled = True
            order_item.cancel_reason = reason
            order_item.cancelled_by = request.user
            order_item.cancelled_at = timezone.now()
            order_item.save(update_fields=['is_cancelled', 'cancel_reason', 'cancelled_by', 'cancelled_at'])

            order = order_item.order
            if not order.items.filter(is_cancelled=False).exists():
                order.status = 'cancelled'
                order.save(update_fields=['status'])
        messages.success(request, f"Cancelled {order_item.menu_item.name}.")

    order = order_item.order
    if order.table_id:
        return redirect(f"{reverse('dashboard:restaurant_table_pos', args=[order.table_id])}?order={order.id}")
    return redirect('dashboard:restaurant_kitchen')

@staff_module_required('restaurant_kitchen')
def restaurant_cancel_order(request, order_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_kitchen')
    order = get_object_or_404(Order, id=order_id)
    reason = request.POST.get('reason', '').strip()

    if not reason:
        messages.error(request, "A reason is required to cancel an order.")
        if order.table_id:
            return redirect('dashboard:restaurant_table_pos', table_id=order.table_id)
        return redirect('dashboard:restaurant_kitchen')

    cancellable = order.items.filter(is_cancelled=False, prep_status__in=['queued', 'preparing'])
    already_served = order.items.filter(is_cancelled=False, prep_status__in=['ready', 'served']).exists()

    with transaction.atomic():
        for order_item in cancellable:
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(
                    quantity_on_hand=stock.quantity_on_hand + order_item.quantity
                )
            order_item.is_cancelled = True
            order_item.cancel_reason = reason
            order_item.cancelled_by = request.user
            order_item.cancelled_at = timezone.now()
            order_item.save(update_fields=['is_cancelled', 'cancel_reason', 'cancelled_by', 'cancelled_at'])

        if already_served:
            messages.success(request, f"Cancelled {cancellable.count()} not-yet-served item(s). Order stays open, some items were already served and must still be paid for.")
        else:
            order.status = 'cancelled'
            order.save(update_fields=['status'])
            messages.success(request, "Order cancelled.")

    if order.table_id:
        return redirect('dashboard:restaurant_table_pos', table_id=order.table_id)
    return redirect('dashboard:restaurant_kitchen')

@staff_module_required('restaurant_kitchen')
def restaurant_set_table_status(request, table_id, new_status):
    if request.method != 'POST' or new_status not in ('available', 'needs_cleaning'):
        return redirect('dashboard:restaurant_kitchen')
    table = get_object_or_404(Table, id=table_id)
    table.status = new_status
    table.save(update_fields=['status'])
    messages.success(request, f"Table {table.number} marked {table.get_status_display()}.")
    return redirect('dashboard:restaurant_kitchen')

@staff_module_required('restaurant_kitchen')
def restaurant_split_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    active_items = order.items.filter(is_cancelled=False)

    if request.method == 'POST':
        selected_ids = request.POST.getlist('item_ids')
        if not selected_ids:
            messages.error(request, "Select at least one item to move to the new bill.")
            return redirect('dashboard:restaurant_split_order', order_id=order.id)

        new_order = Order.objects.create(
            customer_name=f"{order.customer_name} (split)",
            customer_phone=order.customer_phone,
            table=order.table,
            order_type=order.order_type,
            status='confirmed',
        )
        active_items.filter(id__in=selected_ids).update(order=new_order)
        messages.success(request, f"Moved {len(selected_ids)} item(s) to a new bill, Order #{new_order.id}.")
        if order.table_id:
            return redirect('dashboard:restaurant_table_pos', table_id=order.table_id)
        return redirect('dashboard:restaurant_kitchen')

    return render(request, 'dashboard/restaurant_split_order.html', {'order': order, 'active_items': active_items})

@staff_module_required('restaurant_kitchen')
def restaurant_waitlist_view(request):
    if request.method == 'POST':
        WaitlistEntry.objects.create(
            guest_name=request.POST.get('guest_name', '').strip(),
            phone_number=request.POST.get('phone_number', '').strip(),
            party_size=int(request.POST.get('party_size') or 2),
            notes=request.POST.get('notes', '').strip(),
        )
        messages.success(request, "Added to waitlist.")
        return redirect('dashboard:restaurant_waitlist')

    waiting = WaitlistEntry.objects.filter(status='waiting').order_by('created_at')
    return render(request, 'dashboard/restaurant_waitlist.html', {'waiting': waiting})


@staff_module_required('restaurant_kitchen')
def restaurant_waitlist_seat(request, entry_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_waitlist')
    entry = get_object_or_404(WaitlistEntry, id=entry_id)
    entry.status = 'seated'
    entry.seated_at = timezone.now()
    entry.save(update_fields=['status', 'seated_at'])
    messages.success(request, f"{entry.guest_name} seated. Open their table to start the order.")
    return redirect('dashboard:restaurant_waitlist')


@staff_module_required('restaurant_kitchen')
def restaurant_waitlist_cancel(request, entry_id):
    if request.method != 'POST':
        return redirect('dashboard:restaurant_waitlist')
    entry = get_object_or_404(WaitlistEntry, id=entry_id)
    entry.status = 'cancelled'
    entry.save(update_fields=['status'])
    return redirect('dashboard:restaurant_waitlist')


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

### end of restaurant views

### start of bar views
@staff_module_required('bar')
def bar_tabs_view(request):
    today = timezone.localdate()
    open_tabs = BarTab.objects.filter(status='open').order_by('-opened_at')

    if request.method == 'POST':
        tab = BarTab.objects.create(
            customer_name=request.POST.get('customer_name', '').strip() or 'Walk-in',
            phone_number=request.POST.get('phone_number', '').strip(),
        )
        return redirect('dashboard:bar_tab_pos', tab_id=tab.id)

    bar_payments = Payment.objects.filter(order__bar_tab__isnull=False, created_at__date=today)
    today_revenue = bar_payments.aggregate(total=Sum('amount'))['total'] or 0
    today_bar_orders = Order.objects.filter(bar_tab__isnull=False, status='confirmed', created_at__date=today).prefetch_related('items__menu_item')
    today_profit = sum((o.total_profit or Decimal('0')) for o in today_bar_orders)

    bar_stock = StockItem.objects.filter(department='bar', is_active=True)
    low_stock_items = [s for s in bar_stock if s.is_low_stock]
    out_of_stock_items = [s for s in bar_stock if s.quantity_on_hand <= 0]
    
    revenue_by_method = {
        code: bar_payments.filter(method=code).aggregate(total=Sum('amount'))['total'] or 0
        for code, _ in Payment.METHOD_CHOICES
    }
    active_hh = get_active_happy_hour()

    return render(request, 'dashboard/bar_tabs.html', {
        'open_tabs': open_tabs,
        'today_revenue': today_revenue,
        'today_profit': today_profit,
        'low_stock_items': low_stock_items,
        'out_of_stock_items': out_of_stock_items,
        'revenue_by_method': revenue_by_method,
        'active_hh': active_hh,
    })

@staff_module_required('bar')
def bar_tab_pos(request, tab_id):
    tab = get_object_or_404(BarTab, id=tab_id)
    open_orders = tab.open_orders

    selected_order = None
    order_param = request.GET.get('order')
    if order_param and order_param != 'new':
        selected_order = next((o for o in open_orders if str(o.id) == order_param), None)

    drinks = MenuItem.objects.filter(
        item_type='drink', serving_point='bar', is_available=True,
        stock_item__isnull=False,          # ghost products can never reach the POS
        stock_item__quantity_on_hand__gt=0,
    ).select_related('stock_item').prefetch_related('images').order_by('category', 'name')

    low_stock_drinks = [d for d in drinks if d.is_low_stock]
    
    drinks = [d for d in drinks if d.is_in_stock]
    categories = sorted(set(d.category for d in drinks if d.category))
    active_hh = get_active_happy_hour()

    return render(request, 'dashboard/bar_tab_pos.html', {
        'tab': tab,
        'open_orders': open_orders,
        'selected_order': selected_order,
        'drinks': drinks,
        'categories': categories,
        'active_hh': active_hh,
    })

def _happy_hour_price(menu_item, tier, active_hh):
    base = menu_item.vip_price if (tier == 'vip' and menu_item.vip_price is not None) else menu_item.regular_price
    if active_hh:
        discount = Decimal(active_hh.discount_percent) / Decimal(100)
        return (base * (1 - discount)).quantize(Decimal('0.01'))
    return base

@staff_module_required('bar')
def bar_add_items(request, tab_id):
    if request.method != 'POST':
        return redirect('dashboard:bar')
    tab = get_object_or_404(BarTab, id=tab_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    order_id = data.get('order_id')
    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'Add at least one item.'}, status=400)

    active_hh = get_active_happy_hour()

    resolved = []
    for item in items:
        try:
            menu_item = MenuItem.objects.select_related('stock_item').get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        tier = item.get('tier') if item.get('tier') in ('regular', 'vip') else 'regular'
        qty = max(int(item.get('qty', 1)), 1)
        price = _happy_hour_price(menu_item, tier, active_hh)
        resolved.append((menu_item, tier, qty, price))

    shortfalls = [mi.name for mi, _, qty, _ in resolved if getattr(mi, 'stock_item', None) and mi.stock_item.quantity_on_hand < qty]
    if shortfalls:
        return JsonResponse({'error': f"Insufficient stock: {', '.join(shortfalls)}."}, status=409)

    with transaction.atomic():
        if order_id:
            order = get_object_or_404(Order, id=order_id, bar_tab=tab)
        else:
            order = Order.objects.create(
                customer_name=tab.customer_name, customer_phone=tab.phone_number,
                bar_tab=tab, order_type='dine_in', status='confirmed',
            )
        for menu_item, tier, qty, price in resolved:
            OrderItem.objects.create(
                order=order, menu_item=menu_item, tier=tier, quantity=qty,
                unit_price_override=price if active_hh else None,
                prep_status='ready' if menu_item.is_quick_serve else 'queued',
            )
            stock = getattr(menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand - qty)

    return JsonResponse({'success': True, 'order_id': order.id})

@staff_module_required('bar')
def bar_advance_item(request, item_id, new_status):
    if request.method != 'POST':
        return redirect('dashboard:bar')
    order_item = get_object_or_404(OrderItem, id=item_id)
    valid_next = {'queued': 'preparing', 'preparing': 'ready', 'ready': 'served'}
    if valid_next.get(order_item.prep_status) != new_status:
        messages.error(request, "That status change isn't valid from the item's current state.")
    else:
        order_item.prep_status = new_status
        order_item.save(update_fields=['prep_status'])
    tab = order_item.order.bar_tab
    return redirect(f"{reverse('dashboard:bar_tab_pos', args=[tab.id])}?order={order_item.order_id}")

@staff_module_required('bar')
def bar_cancel_item(request, item_id):
    if request.method != 'POST':
        return redirect('dashboard:bar')
    order_item = get_object_or_404(OrderItem, id=item_id)
    reason = request.POST.get('reason', '').strip()

    if not reason:
        messages.error(request, "A reason is required to cancel an item.")
    elif order_item.prep_status not in ('queued', 'preparing'):
        messages.error(request, f"Can't cancel {order_item.menu_item.name}, it's already {order_item.get_prep_status_display().lower()}.")
    else:
        with transaction.atomic():
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand + order_item.quantity)
            order_item.is_cancelled = True
            order_item.cancel_reason = reason
            order_item.cancelled_by = request.user
            order_item.cancelled_at = timezone.now()
            order_item.save(update_fields=['is_cancelled', 'cancel_reason', 'cancelled_by', 'cancelled_at'])

            order = order_item.order
            if not order.items.filter(is_cancelled=False).exists():
                order.status = 'cancelled'
                order.save(update_fields=['status'])
        messages.success(request, f"Cancelled {order_item.menu_item.name}.")

    tab = order_item.order.bar_tab
    return redirect(f"{reverse('dashboard:bar_tab_pos', args=[tab.id])}?order={order_item.order_id}")

@staff_module_required('bar')
def bar_cancel_order(request, order_id):
    if request.method != 'POST':
        return redirect('dashboard:bar')
    order = get_object_or_404(Order, id=order_id)
    reason = request.POST.get('reason', '').strip()
    tab = order.bar_tab

    if not reason:
        messages.error(request, "A reason is required to cancel an order.")
        return redirect('dashboard:bar_tab_pos', tab_id=tab.id)

    cancellable = order.items.filter(is_cancelled=False, prep_status__in=['queued', 'preparing'])
    already_served = order.items.filter(is_cancelled=False, prep_status__in=['ready', 'served']).exists()

    with transaction.atomic():
        for order_item in cancellable:
            stock = getattr(order_item.menu_item, 'stock_item', None)
            if stock:
                StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand + order_item.quantity)
            order_item.is_cancelled = True
            order_item.cancel_reason = reason
            order_item.cancelled_by = request.user
            order_item.cancelled_at = timezone.now()
            order_item.save(update_fields=['is_cancelled', 'cancel_reason', 'cancelled_by', 'cancelled_at'])

        if already_served:
            messages.success(request, f"Cancelled {cancellable.count()} not-yet-served item(s). Order stays open.")
        else:
            order.status = 'cancelled'
            order.save(update_fields=['status'])
            messages.success(request, "Order cancelled.")

    return redirect('dashboard:bar_tab_pos', tab_id=tab.id)

@staff_module_required('bar')
def bar_record_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id, bar_tab__isnull=False)

    if not order.is_fully_served:
        messages.error(request, "This order can't be paid yet, every drink must be marked Served first.")
        return redirect('dashboard:bar_tab_pos', tab_id=order.bar_tab_id)

    if request.method == 'POST':
        instance = Payment(order=order)
        form = PaymentForm(request.POST, instance=instance)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.received_by = request.user
            payment.save()

            tab = order.bar_tab
            if not tab.open_orders:
                tab.status = 'closed'
                tab.closed_at = timezone.now()
                tab.save(update_fields=['status', 'closed_at'])
                messages.success(request, f"Payment recorded. Tab for {tab.customer_name} is now closed.")
            else:
                if payment.change_given > 0:
                    messages.success(request, f"Payment recorded. CHANGE DUE: KSh {payment.change_given:,.0f}")
                else:
                    messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded.")
            return redirect('dashboard:bar')
    else:
        form = PaymentForm(initial={'amount': order.balance_due})

    return render(request, 'dashboard/bar_payment.html', {'form': form, 'order': order})

@staff_module_required('bar')
def bar_wastage_log(request):
    if request.method == 'POST':
        try:
            stock_item = StockItem.objects.get(id=request.POST.get('stock_item'), department='bar')
            quantity = Decimal(request.POST.get('quantity'))
            reason = request.POST.get('reason', '').strip()
        except (StockItem.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Invalid entry.")
            return redirect('dashboard:bar_wastage')

        if not reason:
            messages.error(request, "A reason is required.")
        elif quantity > stock_item.quantity_on_hand:
            messages.error(request, f"Can't log more waste than is in stock ({stock_item.quantity_on_hand} {stock_item.unit}).")
        else:
            WastageLog.objects.create(stock_item=stock_item, quantity=quantity, reason=reason, logged_by=request.user)
            messages.success(request, "Wastage logged and stock adjusted.")
        return redirect('dashboard:bar_wastage')

    stock_items = StockItem.objects.filter(department='bar', is_active=True)
    recent = WastageLog.objects.filter(stock_item__department='bar').select_related('stock_item', 'logged_by').order_by('-logged_at')[:20]
    return render(request, 'dashboard/bar_wastage.html', {'stock_items': stock_items, 'recent': recent})

@staff_module_required('bar')
def bar_export_pdf(request):
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    orders_qs = Order.objects.filter(
        bar_tab__isnull=False, status='confirmed'
    ).prefetch_related('items__menu_item', 'payments').order_by('created_at')
    if date_from:
        orders_qs = orders_qs.filter(created_at__date__gte=date_from)
    if date_to:
        orders_qs = orders_qs.filter(created_at__date__lte=date_to)

    orders = list(orders_qs)

    grand_total = sum((o.total_amount for o in orders), Decimal('0'))
    total_cost = sum((o.total_cost or Decimal('0')) for o in orders)
    total_profit = grand_total - total_cost
    total_paid = sum((o.amount_paid for o in orders), Decimal('0'))
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')
    vat_amount = grand_total - subtotal_excl_vat
    margin_pct = (total_profit / grand_total * 100) if grand_total else Decimal('0')

    html_string = render_to_string('dashboard/bar_report_pdf.html', {
        'orders': orders,
        'date_from': date_from,
        'date_to': date_to,
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': vat_amount,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'total_paid': total_paid,
        'total_outstanding': grand_total - total_paid,
        'margin_pct': margin_pct,
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="bar_sales_{timezone.now().date()}.pdf"'
    return response


#### end of bar views

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

#### end of store views
#### start of receipt views
import base64
import io
import qrcode
from .permissions import can_access
from django.conf import settings


@login_required
def order_receipt(request, order_id):
    if not (can_access(request.user, 'restaurant_kitchen') or can_access(request.user, 'bar') or can_access(request.user, 'reception')):
        return render(request, 'dashboard/restricted.html', {'module_label': 'Receipts'}, status=403)

    order = get_object_or_404(
        Order.objects.prefetch_related('items__menu_item', 'payments__received_by'), id=order_id
    )

    active_items = [i for i in order.items.all() if not i.is_cancelled]
    total = order.total_amount
    subtotal_excl_vat = total / (1 + VAT_RATE) if total else Decimal('0')
    vat_amount = total - subtotal_excl_vat
    payments = list(order.payments.all())
    total_change = sum((p.change_given for p in payments), Decimal('0'))

    receipt_url = request.build_absolute_uri()
    qr = qrcode.make(receipt_url)
    buffer = io.BytesIO()
    qr.save(buffer, format='PNG')
    qr_b64 = base64.b64encode(buffer.getvalue()).decode()

    return render(request, 'dashboard/order_receipt.html', {
        'order': order,
        'active_items': active_items,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': vat_amount,
        'total': total,
        'payments': payments,
        'total_change': total_change,
        'qr_b64': qr_b64,
        'kra_pin': settings.HOTEL_KRA_PIN,
        'hotel_address': settings.HOTEL_ADDRESS,
        'hotel_phone': settings.HOTEL_PHONE,
    })
    
@staff_module_required('reception')
def booking_bill(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('room_type', 'conference_room', 'assigned_room')
                       .prefetch_related('payments__received_by', 'room_service_orders__items__menu_item'),
        id=booking_id
    )

    room_charge = booking.total_amount
    service_orders = [o for o in booking.room_service_orders.all() if o.status != 'cancelled']
    service_total = sum((o.total_amount for o in service_orders), Decimal('0'))

    grand_total = room_charge + service_total
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')
    vat_amount = grand_total - subtotal_excl_vat

    booking_payments = list(booking.payments.all())
    service_payments = [p for o in service_orders for p in o.payments.all()]
    all_payments = booking_payments + service_payments
    total_paid = sum((p.amount for p in all_payments), Decimal('0'))
    total_change = sum((p.change_given for p in all_payments), Decimal('0'))

    qr = qrcode.make(request.build_absolute_uri())
    buffer = io.BytesIO()
    qr.save(buffer, format='PNG')
    qr_b64 = base64.b64encode(buffer.getvalue()).decode()

    return render(request, 'dashboard/booking_bill.html', {
        'booking': booking,
        'room_charge': room_charge,
        'service_orders': service_orders,
        'service_total': service_total,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': vat_amount,
        'all_payments': all_payments,
        'total_paid': total_paid,
        'total_change': total_change,
        'balance_due': grand_total - total_paid,
        'qr_b64': qr_b64,
        'kra_pin': settings.HOTEL_KRA_PIN,
        'hotel_address': settings.HOTEL_ADDRESS,
        'hotel_phone': settings.HOTEL_PHONE,
    })