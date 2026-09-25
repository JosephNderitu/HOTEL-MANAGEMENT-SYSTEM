import uuid
import json
import base64
import io
import qrcode
from django.conf import settings
from django.contrib.auth.decorators import login_required
from .decorators import *

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, F, Sum, ExpressionWrapper, DecimalField, Count, Sum as DjangoSum
from .models import *
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
from store.forms import *
from django.db import transaction
from store.services import *
from .services import *

from decimal import Decimal
from django.urls import reverse
from datetime import timedelta, datetime, time
from .permissions import *

from django.utils.dateparse import parse_date

from django.contrib.auth.models import User, Group
from hrm.models import *
from hrm.services import is_within_geofence
import calendar as cal_module

from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

COURSE_PRIORITY = {'starter': 0, 'main': 1, 'dessert': 2}

def _parse_date_input(date_str, fallback):
    if not date_str:
        return fallback
    # 1. Try standard YYYY-MM-DD
    d = parse_date(date_str)
    if d:
        return d
    # 2. Try human-readable formats sent from UI/URL params
    for fmt in ('%b. %d, %Y', '%b %d, %Y', '%B %d, %Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return fallback


def _local_day_bounds(date_from, date_to):
    """Half-open [start, end) covering whole local days, in the project timezone."""
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timezone.timedelta(days=1), time.min)
    if settings.USE_TZ:
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(start, tz)
        end = timezone.make_aware(end, tz)
    return start, end


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

    model = Booking if target_type == 'booking' else Order
    target = get_object_or_404(model, id=target_id)

    if request.method == 'POST':
        with transaction.atomic():
            locked_target = model.objects.select_for_update().get(id=target.id)
            remaining = locked_target.total_balance_due if target_type == 'booking' else locked_target.balance_due
            if remaining <= 0:
                messages.error(request, f"This {target_type} is already fully paid — no new payment was recorded.", extra_tags='swal-error')
                return redirect('dashboard:reception')

            instance = Payment(booking=locked_target) if target_type == 'booking' else Payment(order=locked_target)
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

# ==============================================================================
# ROOMS & HOUSEKEEPING MANAGEMENT
# ==============================================================================

@staff_module_required('rooms')
def rooms_view(request):
    status_filter = request.GET.get('status', '')
    rooms = Room.objects.select_related('room_type').order_by('room_type__name', 'number')
    if status_filter:
        rooms = rooms.filter(status=status_filter)

    counts = {s: Room.objects.filter(status=s).count() for s in ('available', 'occupied', 'cleaning', 'maintenance')}
    open_maintenance_count = MaintenanceRequest.objects.filter(status__in=['open', 'in_progress']).count()
    unclaimed_lost_count = LostFoundItem.objects.filter(status='unclaimed').count()
    active_bookings = {b.assigned_room_id: b for b in Booking.objects.filter(status='checked_in', assigned_room__isnull=False)}

    today = timezone.localdate()
    day_start, day_end = _local_day_bounds(today, today)
    today_payments = Payment.objects.filter(booking__isnull=False, created_at__gte=day_start, created_at__lt=day_end)
    today_revenue = today_payments.aggregate(total=Sum('amount'))['total'] or 0
    revenue_by_method = {
        code: today_payments.filter(method=code).aggregate(total=Sum('amount'))['total'] or 0
        for code, _ in Payment.METHOD_CHOICES
    }
    bank_total = (revenue_by_method.get('bank_equity', 0) + revenue_by_method.get('bank_family', 0) + revenue_by_method.get('bank_coop', 0))

    return render(request, 'dashboard/rooms.html', {
        'rooms': rooms,
        'counts': counts,
        'status_filter': status_filter,
        'open_maintenance_count': open_maintenance_count,
        'unclaimed_lost_count': unclaimed_lost_count,
        'active_bookings': active_bookings,
        'today_revenue': today_revenue,
        'revenue_by_method': revenue_by_method,
        'bank_total': bank_total,
        'trend_url': reverse('dashboard:rooms_revenue_trend'),
    })

@staff_module_required('rooms')
def room_detail(request, room_id):
    room = get_object_or_404(Room.objects.select_related('room_type'), id=room_id)
    booking = Booking.objects.filter(assigned_room=room, status='checked_in').select_related('room_type').prefetch_related(
        'room_service_orders__items__menu_item', 'payments'
    ).first()

    can_manage = can_manage_bookings_from_rooms(request.user)
    active_cleaning = room.cleaning_logs.filter(completed_at__isnull=True).first()
    open_maintenance = room.maintenance_requests.filter(status__in=['open', 'in_progress'])

    checkin_form = RoomCheckinForm(initial={'check_out': timezone.localdate() + timezone.timedelta(days=1)})

    menu_items, categories = [], []
    if booking:
        drinks = MenuItem.objects.filter(item_type='drink', serving_point='kitchen', is_available=True).select_related('stock_item').prefetch_related('images')
        food = MenuItem.objects.filter(item_type='food', is_available=True).select_related('stock_item').prefetch_related('images')
        menu_items = [i for i in list(food) + list(drinks) if i.is_in_stock]
        categories = sorted(set(i.category for i in menu_items if i.category))

    return render(request, 'dashboard/room_detail.html', {
        'room': room, 
        'booking': booking, 
        'can_manage': can_manage,
        'active_cleaning': active_cleaning, 
        'open_maintenance': open_maintenance,
        'checkin_form': checkin_form, 
        'menu_items': menu_items, 
        'categories': categories,
    })

@staff_module_required('rooms')
def room_quick_checkin(request, room_id):
    if not can_manage_bookings_from_rooms(request.user):
        messages.error(request, "You don't have permission to check in guests.")
        return redirect('dashboard:room_detail', room_id=room_id)
    
    room = get_object_or_404(Room, id=room_id, status='available')
    if request.method != 'POST':
        return redirect('dashboard:room_detail', room_id=room_id)

    form = RoomCheckinForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Fill in all required fields correctly.")
        return redirect('dashboard:room_detail', room_id=room_id)

    check_in, check_out = timezone.localdate(), form.cleaned_data['check_out']
    amount = form.cleaned_data['amount']
    if check_out <= check_in:
        messages.error(request, "Check-out must be after today.")
        return redirect('dashboard:room_detail', room_id=room_id)
    if not (room.room_type.price_min <= amount <= room.room_type.price_max):
        messages.error(request, f"Rate must be between KSh {room.room_type.price_min:,.0f} and KSh {room.room_type.price_max:,.0f}.")
        return redirect('dashboard:room_detail', room_id=room_id)

    booking = Booking.objects.create(
        booking_type='room', 
        guest_name=form.cleaned_data['guest_name'], 
        guest_phone=form.cleaned_data['guest_phone'],
        guest_id_no=form.cleaned_data.get('guest_id_no', ''), 
        room_type=room.room_type, 
        assigned_room=room,
        check_in=check_in, 
        check_out=check_out, 
        amount=amount, 
        status='checked_in', 
        source='walkin',
    )
    room.status = 'occupied'
    room.save(update_fields=['status'])
    messages.success(request, f"{booking.guest_name} checked in to Room {room.number}.")
    return redirect('dashboard:room_detail', room_id=room_id)

def _room_checkout_view(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    booking = get_object_or_404(Booking, assigned_room=room, status='checked_in')

    if booking.total_balance_due > 0:
        return redirect('dashboard:room_settle_stay', booking_id=booking.id)

    booking.status = 'checked_out'
    booking.save(update_fields=['status'])
    room.status = 'cleaning'
    room.save(update_fields=['status'])
    messages.success(request, f"{booking.guest_name} checked out of Room {room.number}.")
    return redirect('dashboard:room_detail', room_id=room_id)

@staff_module_required('rooms')
def room_checkout(request, room_id):
    if not can_manage_bookings_from_rooms(request.user):
        messages.error(request, "You don't have permission to check out guests.")
        return redirect('dashboard:room_detail', room_id=room_id)
    return _room_checkout_view(request, room_id)

@staff_module_required('rooms')
def room_settle_stay(request, booking_id):
    if not can_manage_bookings_from_rooms(request.user):
        messages.error(request, "You don't have permission to settle payments.")
        return redirect('dashboard:rooms')
    
    booking = get_object_or_404(Booking, id=booking_id)
    response = _settle_stay_view(request, booking_id, back_url_name='dashboard:rooms')
    
    if request.method == 'POST' and booking.total_balance_due <= 0 and booking.status == 'checked_in':
        booking.refresh_from_db()
        if booking.total_balance_due <= 0:
            booking.status = 'checked_out'
            booking.save(update_fields=['status'])
            if booking.assigned_room:
                booking.assigned_room.status = 'cleaning'
                booking.assigned_room.save(update_fields=['status'])
    return response

@staff_module_required('rooms')
def room_add_service_order(request, room_id):
    if not can_manage_bookings_from_rooms(request.user):
        return JsonResponse({'error': "You don't have permission to place orders."}, status=403)
    if request.method != 'POST':
        return redirect('dashboard:room_detail', room_id=room_id)

    room = get_object_or_404(Room, id=room_id)
    booking = get_object_or_404(Booking, assigned_room=room, status='checked_in')

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'Add at least one item.'}, status=400)

    order = Order.objects.create(
        customer_name=booking.guest_name, 
        customer_phone=booking.guest_phone, 
        room_booking=booking, 
        status='pending'
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

@staff_module_required('rooms')
def room_cleaning_checklist(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    if room.status != 'cleaning':
        messages.error(request, f"Room {room.number} is not currently in Cleaning status.")
        return redirect('dashboard:room_detail', room_id=room.id)

    log = room.cleaning_logs.filter(completed_at__isnull=True).order_by('-started_at').first()
    if not log:
        log = RoomCleaningLog.objects.create(room=room, started_by=request.user)

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

    # NEW
    with transaction.atomic():
        MaintenanceRequest.objects.create(
            room=room,
            issue=issue,
            description=request.POST.get('description', '').strip(),
            priority=request.POST.get('priority', 'medium'),
            reported_by=request.user,
        )
        if room.status == 'occupied':
            messages.warning(request, f"Room {room.number} has a guest in it, so it stays Occupied. It will need attention at checkout.")
        else:
            room.status = 'maintenance'
            room.save(update_fields=['status'])
            messages.success(request, f"Issue reported. Room {room.number} is now under Maintenance.")
    return redirect('dashboard:rooms')

@staff_module_required('rooms')
def room_transfer(request, room_id):
    if not can_manage_bookings_from_rooms(request.user):
        messages.error(request, "You don't have permission to transfer guests.")
        return redirect('dashboard:room_detail', room_id=room_id)

    from_room = get_object_or_404(Room, id=room_id)
    booking = get_object_or_404(Booking, assigned_room=from_room, status='checked_in')
    available_rooms = Room.objects.filter(status='available').exclude(id=from_room.id).select_related('room_type')

    if request.method == 'POST':
        to_room = get_object_or_404(Room, id=request.POST.get('to_room'), status='available')
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "A reason is required for the transfer record.")
            return redirect('dashboard:room_transfer', room_id=from_room.id)

        new_rate = booking.amount
        if to_room.room_type_id != booking.room_type_id:
            try:
                new_rate = Decimal(request.POST.get('new_rate') or '0')
            except Exception:
                new_rate = Decimal('0')
            if not (to_room.room_type.price_min <= new_rate <= to_room.room_type.price_max):
                messages.error(request, f"New rate must be between KSh {to_room.room_type.price_min:,.0f} and KSh {to_room.room_type.price_max:,.0f} for {to_room.room_type.name}.")
                return redirect('dashboard:room_transfer', room_id=from_room.id)

        with transaction.atomic():
            RoomTransferLog.objects.create(booking=booking, from_room=from_room, to_room=to_room, reason=reason, transferred_by=request.user)
            from_room.status = 'cleaning'
            from_room.save(update_fields=['status'])
            to_room.status = 'occupied'
            to_room.save(update_fields=['status'])
            booking.assigned_room = to_room
            booking.room_type = to_room.room_type
            booking.amount = new_rate
            booking.save(update_fields=['assigned_room', 'room_type', 'amount'])

        messages.success(request, f"{booking.guest_name} moved from Room {from_room.number} to Room {to_room.number}.")
        return redirect('dashboard:room_detail', room_id=to_room.id)

    return render(request, 'dashboard/room_transfer.html', {
        'from_room': from_room, 'booking': booking, 'available_rooms': available_rooms,
    })
    
@staff_module_required('rooms')
def room_extend_stay(request, room_id):
    if not can_manage_bookings_from_rooms(request.user):
        messages.error(request, "You don't have permission to extend a stay.")
        return redirect('dashboard:room_detail', room_id=room_id)

    room = get_object_or_404(Room, id=room_id)
    booking = get_object_or_404(Booking, assigned_room=room, status='checked_in')

    if request.method == 'POST':
        try:
            new_checkout = datetime.strptime(request.POST.get('new_checkout', ''), '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, "Enter a valid date.")
            return redirect('dashboard:room_detail', room_id=room_id)

        if new_checkout <= booking.check_out:
            messages.error(request, f"New check-out must be after the current one ({booking.check_out}).")
            return redirect('dashboard:room_detail', room_id=room_id)

        booking.check_out = new_checkout
        booking.save(update_fields=['check_out'])
        messages.success(request, f"{booking.guest_name}'s stay extended to {new_checkout}.")

    return redirect('dashboard:room_detail', room_id=room_id)

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
    # NEW
    req.save(update_fields=['status', 'resolved_by', 'resolved_at'])

    still_open = req.room.maintenance_requests.filter(status__in=['open', 'in_progress']).exists()
    if not still_open and req.room.status == 'maintenance':
        req.room.status = 'cleaning'
        req.room.save(update_fields=['status'])
        messages.success(request, f"Resolved. Room {req.room.number} moved to Cleaning before it can go Available.")
    elif still_open:
        messages.success(request, f"Resolved. Room {req.room.number} still has other open issues, so it stays under Maintenance.")
    else:
        messages.success(request, "Resolved.")
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

@staff_module_required('rooms')
def rooms_export_pdf(request):
    date_from, date_to = _report_range(request)
    range_start, range_end = _local_day_bounds(date_from, date_to)

    payments = list(Payment.objects.filter(
        booking__isnull=False,
        created_at__gte=range_start, created_at__lt=range_end,
    ).select_related('booking', 'booking__room_type', 'booking__assigned_room', 'booking__conference_room').order_by('created_at'))

    days = _group_payments_by_day(payments)
    grand_total = sum((p.amount for p in payments), Decimal('0'))
    total_change = sum((p.change_given for p in payments), Decimal('0'))
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')

    method_totals = {}
    for d in days:
        for m, v in d['by_method'].items():
            method_totals[m] = method_totals.get(m, Decimal('0')) + v

    by_type, by_room = {}, {}
    for p in payments:
        b = p.booking
        type_name = b.room_type.name if b.room_type else "Conference / Other"
        by_type[type_name] = by_type.get(type_name, Decimal('0')) + p.amount
        if b.assigned_room:
            room_label = f"Room {b.assigned_room.number}"
        elif b.conference_room:
            room_label = b.conference_room.name
        else:
            room_label = "Unassigned"
        by_room[room_label] = by_room.get(room_label, Decimal('0')) + p.amount

    type_sorted = sorted(by_type.items(), key=lambda x: x[1], reverse=True)
    type_max = type_sorted[0][1] if type_sorted else Decimal('1')
    type_rows = [{'label': n, 'amount': a, 'pct': float(a / type_max * 100)} for n, a in type_sorted]

    room_sorted = sorted(by_room.items(), key=lambda x: x[1], reverse=True)[:10]
    room_max = room_sorted[0][1] if room_sorted else Decimal('1')
    room_rows = [{'label': n, 'amount': a, 'pct': float(a / room_max * 100)} for n, a in room_sorted]

    unpaid_bookings = [b for b in Booking.objects.filter(
        status__in=['confirmed', 'checked_in', 'checked_out'],
        created_at__gte=range_start, created_at__lt=range_end,
    ).select_related('room_type', 'assigned_room') if b.total_balance_due > 0]

    span = (date_to - date_from).days + 1
    html_string = render_to_string('dashboard/rooms_report_pdf.html', {
        'days': days, 'date_from': date_from, 'date_to': date_to,
        'payment_count': len(payments),
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': grand_total - subtotal_excl_vat,
        'total_change': total_change,
        'method_totals': method_totals,
        'daily_average': grand_total / span if span else Decimal('0'),
        'type_rows': type_rows,
        'room_rows': room_rows,
        'unpaid_bookings': unpaid_bookings,
        'total_outstanding': sum((b.total_balance_due for b in unpaid_bookings), Decimal('0')),
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="rooms_sales_{date_from}_to_{date_to}.pdf"'
    return response

@staff_module_required('rooms')
def rooms_revenue_trend(request):
    date_from, date_to = _report_range(request, default_days=6)
    return _trend_payload(Payment.objects.filter(booking__isnull=False), date_from, date_to)
#### end of room views ####

### start of restaurant views
@staff_module_required('restaurant_kitchen')
def restaurant_tables_view(request):
    tables = Table.objects.all().order_by('number')
    today = timezone.localdate()

    # NEW
    restaurant_payments = Payment.objects.filter(
        order__room_booking__isnull=True, order__bar_tab__isnull=True, created_at__date=today
    )
    today_revenue = restaurant_payments.aggregate(total=Sum('amount'))['total'] or 0
    revenue_by_method = {
        code: restaurant_payments.filter(method=code).aggregate(total=Sum('amount'))['total'] or 0
        for code, _ in Payment.METHOD_CHOICES
    }

    # NEW
    offsite_qs = Order.objects.filter(
        room_booking__isnull=True, table__isnull=True, bar_tab__isnull=True, status='confirmed'
    ).prefetch_related('items__menu_item').order_by('-created_at')
    offsite_open_orders = [o for o in offsite_qs if o.balance_due > 0]
    waiting_count = WaitlistEntry.objects.filter(status='waiting').count()

    return render(request, 'dashboard/restaurant_tables.html', {
        'tables': tables,
        'today_revenue': today_revenue,
        'revenue_by_method': revenue_by_method,
        'offsite_open_orders': offsite_open_orders,
        'waiting_count': waiting_count,
        'bank_total': (revenue_by_method.get('bank_equity', 0)
                       + revenue_by_method.get('bank_family', 0)
                       + revenue_by_method.get('bank_coop', 0)),
        'trend_url': reverse('dashboard:restaurant_revenue_trend')
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

# ==============================================================================
# DAILY USAGE LOGS (Generic Helpers & Views for Kitchen & Housekeeping)
# ==============================================================================

def _can_touch_usage_log(user, department):
    module_map = {'kitchen': 'restaurant_kitchen', 'housekeeping': 'rooms', 'bar': 'bar'}
    module = module_map.get(department, department)
    return can_access(user, module) or can_access(user, 'store')

def _usage_log_view(request, department, template_name, back_url_name):
    if not _can_touch_usage_log(request.user, department):
        return render(request, 'dashboard/restricted.html', {'module_label': 'Daily Usage Log'}, status=403)

    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department=department, date=today)
    stock_items = StockItem.objects.filter(department=department, is_active=True)
    items = log.items.select_related('stock_item', 'added_by', 'confirmed_by').order_by('-added_at')

    return render(request, template_name, {
        'log': log,
        'stock_items': stock_items,
        'items': items,
        'unlocked_count': items.filter(is_locked=False).count(),
        'department': department,
        'back_url_name': back_url_name,
    })

def _usage_log_add(request, department):
    if request.method != 'POST' or not _can_touch_usage_log(request.user, department):
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department=department, date=today)

    stock_item_id = request.POST.get('stock_item')
    if not stock_item_id:
        messages.error(request, "Select an item from stock. If it's not listed yet, it needs to be logged as a purchase first.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    try:
        quantity = Decimal(request.POST.get('quantity') or '0')
    except Exception:
        quantity = Decimal('0')
    if quantity <= 0:
        messages.error(request, "Enter a quantity greater than zero.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    with transaction.atomic():
        stock = get_object_or_404(StockItem, id=stock_item_id, department=department)
        if quantity > stock.quantity_on_hand:
            messages.error(request, f"Can't log more than what's in stock ({stock.quantity_on_hand} {stock.unit}).")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))
        DailyUsageItem.objects.create(log=log, stock_item=stock, quantity=quantity, added_by=request.user)
        StockItem.objects.filter(id=stock.id).select_for_update().update(quantity_on_hand=stock.quantity_on_hand - quantity)
        notify_low_stock_if_needed(stock)

    messages.success(request, "Usage logged.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

def _usage_log_delete(request, item_id, department):
    if request.method != 'POST' or not _can_touch_usage_log(request.user, department):
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    item = get_object_or_404(DailyUsageItem, id=item_id, log__department=department)
    if item.is_locked:
        messages.error(request, "This entry is already confirmed and locked, it can't be removed here.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    with transaction.atomic():
        if item.stock_item:
            StockItem.objects.filter(id=item.stock_item.id).select_for_update().update(
                quantity_on_hand=item.stock_item.quantity_on_hand + item.quantity
            )
        item.delete()

    messages.success(request, "Entry removed.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

def _usage_log_confirm(request, department):
    if request.method != 'POST' or not _can_touch_usage_log(request.user, department):
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    today = timezone.localdate()
    log, _ = DailyUsageLog.objects.get_or_create(department=department, date=today)
    unlocked = log.items.filter(is_locked=False)
    count = unlocked.count()

    if count == 0:
        messages.error(request, "Nothing new to confirm right now.")
    else:
        unlocked.update(
            is_locked=True,
            confirmed_by=request.user,
            confirmed_at=timezone.now()
        )
        messages.success(
            request,
            f"Confirmed and locked {count} entr{'y' if count == 1 else 'ies'}. You can still add more usage later today."
        )

    return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

# ------------------------------------------------------------------------------
# KITCHEN USAGE LOG ENDPOINTS
# ------------------------------------------------------------------------------

@login_required
def kitchen_usage_log_view(request):
    return _usage_log_view(request, 'kitchen', 'dashboard/kitchen_usage_log.html', 'dashboard:restaurant_kitchen_display')

@login_required
def kitchen_usage_log_add(request):
    return _usage_log_add(request, 'kitchen')

@login_required
def kitchen_usage_log_delete(request, item_id):
    return _usage_log_delete(request, item_id, 'kitchen')

@login_required
def kitchen_usage_log_confirm(request):
    return _usage_log_confirm(request, 'kitchen')

# ------------------------------------------------------------------------------
# BAR USAGE LOG ENDPOINTS
# ------------------------------------------------------------------------------

@login_required
def bar_usage_log_view(request):
    return _usage_log_view(
        request, 'bar', 'dashboard/bar_usage_log.html', 'dashboard:bar'
    )

@login_required
def bar_usage_log_add(request):
    return _usage_log_add(request, 'bar')

@login_required
def bar_usage_log_delete(request, item_id):
    return _usage_log_delete(request, item_id, 'bar')

@login_required
def bar_usage_log_confirm(request):
    return _usage_log_confirm(request, 'bar')


# ------------------------------------------------------------------------------
# HOUSEKEEPING USAGE LOG ENDPOINTS
# ------------------------------------------------------------------------------

@login_required
def housekeeping_usage_log_view(request):
    return _usage_log_view(request, 'housekeeping', 'dashboard/housekeeping_usage_log.html', 'dashboard:rooms')

@login_required
def housekeeping_usage_log_add(request):
    return _usage_log_add(request, 'housekeeping')

@login_required
def housekeeping_usage_log_delete(request, item_id):
    return _usage_log_delete(request, item_id, 'housekeeping')

@login_required
def housekeeping_usage_log_confirm(request):
    return _usage_log_confirm(request, 'housekeeping')

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


# NEW
@staff_module_required('restaurant_kitchen')
def restaurant_record_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.room_booking_id or order.bar_tab_id:
        messages.error(request, "This order belongs to another department and can't be settled from Restaurant & Kitchen.", extra_tags='swal-error')
        return redirect('dashboard:restaurant_kitchen')

    if not order.is_fully_served:
        messages.error(request, _order_not_served_message(order), extra_tags='swal-warning')
        return redirect('dashboard:restaurant_kitchen')

    if request.method == 'POST':
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(id=order.id)
            if locked_order.balance_due <= 0:
                messages.error(request, f"Order #{locked_order.id} is already fully paid — no new payment was recorded.", extra_tags='swal-error')
                return redirect('dashboard:restaurant_kitchen')

            instance = Payment(order=locked_order)
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

    # NEW
    if order.payments.exists():
        messages.error(
            request,
            f"Order #{order.id} already has a payment against it and can't be split. "
            "Cancel and re-enter the items if the bill needs to be divided.",
            extra_tags='swal-error',
        )
        return redirect('dashboard:restaurant_kitchen')

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

def _group_payments_by_day(payments):
    """Cash basis: bucket payments by the local date the money was received."""
    days = {}
    for p in payments:
        d = timezone.localtime(p.created_at).date()
        bucket = days.setdefault(d, {'date': d, 'payments': [], 'total': Decimal('0'), 'by_method': {}})
        bucket['payments'].append(p)
        bucket['total'] += p.amount
        label = p.get_method_display()
        bucket['by_method'][label] = bucket['by_method'].get(label, Decimal('0')) + p.amount
    return [days[d] for d in sorted(days)]

def _report_range(request, default_days=0):
    today = timezone.localdate()
    date_to = _parse_date_input(request.GET.get('date_to'), today)
    date_from = _parse_date_input(request.GET.get('date_from'), date_to - timezone.timedelta(days=default_days))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
        
    if (date_to - date_from).days > 366:
        date_from = date_to - timezone.timedelta(days=366)
    return date_from, date_to

@staff_module_required('restaurant_kitchen')
def restaurant_export_pdf(request):
    date_from, date_to = _report_range(request)

    payments = Payment.objects.filter(
        order__isnull=False,
        order__room_booking__isnull=True,
        order__bar_tab__isnull=True,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).select_related('order', 'order__table', 'received_by').prefetch_related('order__items__menu_item').order_by('created_at')

    payments = list(payments)
    days = _group_payments_by_day(payments)

    grand_total = sum((p.amount for p in payments), Decimal('0'))
    total_change = sum((p.change_given for p in payments), Decimal('0'))
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')

    method_totals = {}
    for d in days:
        for m, v in d['by_method'].items():
            method_totals[m] = method_totals.get(m, Decimal('0')) + v

    unpaid_orders = [o for o in Order.objects.filter(
        room_booking__isnull=True, bar_tab__isnull=True, status='confirmed',
        created_at__date__gte=date_from, created_at__date__lte=date_to,
    ).select_related('table').prefetch_related('items__menu_item', 'payments') if o.balance_due > 0]

    range_start = timezone.make_aware(datetime.combine(date_from, time.min))
    range_end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min))

    # --- Accrual side: what was SOLD in the range ---
    sales_orders = list(Order.objects.filter(
        room_booking__isnull=True, bar_tab__isnull=True, status='confirmed',
        created_at__gte=range_start, created_at__lt=range_end,
    ).select_related('table').prefetch_related('items__menu_item', 'payments').order_by('created_at'))

    sales_days = {}
    for o in sales_orders:
        d = timezone.localtime(o.created_at).date()
        bucket = sales_days.setdefault(d, {'date': d, 'orders': [], 'total': Decimal('0')})
        bucket['orders'].append(o)
        bucket['total'] += o.total_amount
    sales_days = [sales_days[d] for d in sorted(sales_days)]
    total_sales = sum((o.total_amount for o in sales_orders), Decimal('0'))

    # --- Reconciliation ---
    prior_orders = Order.objects.filter(
        room_booking__isnull=True, bar_tab__isnull=True, status='confirmed',
        created_at__lt=range_start,
    ).prefetch_related('items__menu_item', 'payments')
    opening_outstanding = sum((o.balance_due for o in prior_orders if o.balance_due > 0), Decimal('0'))

    span = (date_to - date_from).days + 1
    html_string = render_to_string('dashboard/restaurant_report_pdf.html', {
        'days': days, 'date_from': date_from, 'date_to': date_to,
        'payment_count': len(payments),
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': grand_total - subtotal_excl_vat,
        'total_change': total_change,
        'method_totals': method_totals,
        'daily_average': grand_total / span if span else Decimal('0'),
        'unpaid_orders': unpaid_orders,
        'total_outstanding': sum((o.balance_due for o in unpaid_orders), Decimal('0')),
        'total_sales': total_sales,
        'sales_days': sales_days,
        'opening_outstanding': opening_outstanding,
        'closing_outstanding': opening_outstanding + total_sales - grand_total,
        'collected_from_prior': sum(
            (p.amount for p in payments if timezone.localtime(p.order.created_at).date() < date_from),
            Decimal('0')
        ),
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="restaurant_sales_{date_from}_to_{date_to}.pdf"'
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

    tab_cards = []
    total_outstanding = Decimal('0')
    for tab in open_tabs:
        orders = tab.open_orders
        outstanding = sum((o.balance_due for o in orders), Decimal('0'))
        unserved = sum(
            1 for o in orders for i in o.items.all()
            if not i.is_cancelled and i.prep_status != 'served'
        )
        total_outstanding += outstanding
        tab_cards.append({'tab': tab, 'order_count': len(orders),
                          'outstanding': outstanding, 'unserved_count': unserved})

    return render(request, 'dashboard/bar_tabs.html', {
        'tab_cards': tab_cards,
        'total_outstanding': total_outstanding,
        'today_revenue': today_revenue,
        'today_profit': today_profit,
        'low_stock_items': low_stock_items,
        'out_of_stock_items': out_of_stock_items,
        'revenue_by_method': revenue_by_method,
        'bank_total': (revenue_by_method.get('bank_equity', 0)
                       + revenue_by_method.get('bank_family', 0)
                       + revenue_by_method.get('bank_coop', 0)),
        'active_hh': active_hh,
        'trend_url': reverse('dashboard:bar_revenue_trend'),
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

    # NEW
    return render(request, 'dashboard/bar_tab_pos.html', {
        'tab': tab,
        'open_orders': open_orders,
        'selected_order': selected_order,
        'drinks': drinks,
        'low_stock_drinks': low_stock_drinks,
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
    order = get_object_or_404(Order, id=order_id)

    if not order.bar_tab_id:
        messages.error(request, "This order belongs to another department and can't be settled from the Bar.", extra_tags='swal-error')
        return redirect('dashboard:bar')

    if not order.is_fully_served:
        messages.error(request, _order_not_served_message(order), extra_tags='swal-warning')
        return redirect('dashboard:bar_tab_pos', tab_id=order.bar_tab_id)

    if request.method == 'POST':
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(id=order.id)
            if locked_order.balance_due <= 0:
                messages.error(request, f"Order #{locked_order.id} is already fully paid — no new payment was recorded.", extra_tags='swal-error')
                return redirect('dashboard:bar_tab_pos', tab_id=order.bar_tab_id)

            instance = Payment(order=locked_order)
            form = PaymentForm(request.POST, instance=instance)
            if form.is_valid():
                payment = form.save(commit=False)
                payment.received_by = request.user
                payment.save()

                tab = locked_order.bar_tab
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
    date_from, date_to = _report_range(request)

    payments = list(Payment.objects.filter(
        order__bar_tab__isnull=False,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).select_related('order', 'order__bar_tab', 'received_by').prefetch_related('order__items__menu_item').order_by('created_at'))

    days = _group_payments_by_day(payments)
    grand_total = sum((p.amount for p in payments), Decimal('0'))
    total_change = sum((p.change_given for p in payments), Decimal('0'))
    subtotal_excl_vat = grand_total / (1 + VAT_RATE) if grand_total else Decimal('0')

    method_totals = {}
    for d in days:
        for m, v in d['by_method'].items():
            method_totals[m] = method_totals.get(m, Decimal('0')) + v

    # Margin on the distinct orders settled in this range
    settled = {p.order_id: p.order for p in payments}.values()
    settled_order_value = sum((o.total_amount for o in settled), Decimal('0'))
    total_cost = sum((o.total_cost or Decimal('0')) for o in settled)
    total_profit = settled_order_value - total_cost

    unpaid_orders = [o for o in Order.objects.filter(
        bar_tab__isnull=False, status='confirmed',
        created_at__date__gte=date_from, created_at__date__lte=date_to,
    ).select_related('bar_tab').prefetch_related('items__menu_item', 'payments') if o.balance_due > 0]

    span = (date_to - date_from).days + 1
    html_string = render_to_string('dashboard/bar_report_pdf.html', {
        'days': days, 'date_from': date_from, 'date_to': date_to,
        'payment_count': len(payments),
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_total': grand_total,
        'subtotal_excl_vat': subtotal_excl_vat,
        'vat_amount': grand_total - subtotal_excl_vat,
        'total_change': total_change,
        'method_totals': method_totals,
        'daily_average': grand_total / span if span else Decimal('0'),
        'settled_order_value': settled_order_value,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'margin_pct': (total_profit / settled_order_value * 100) if settled_order_value else Decimal('0'),
        'unpaid_orders': unpaid_orders,
        'total_outstanding': sum((o.balance_due for o in unpaid_orders), Decimal('0')),
    })
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="bar_sales_{date_from}_to_{date_to}.pdf"'
    return response

###used by bar and restaurant
def _order_not_served_message(order):
    counts = {'queued': 0, 'preparing': 0, 'ready': 0}
    for item in order.items.filter(is_cancelled=False):
        if item.prep_status in counts:
            counts[item.prep_status] += 1
    parts = [f"{v} {k}" for k, v in counts.items() if v]
    detail = ", ".join(parts) if parts else "not yet fully served"
    return f"Order #{order.id} can't be paid yet — {detail}. Every item must be marked Served first."

def _trend_payload(payment_qs, date_from, date_to):
    totals = {
        row['created_at__date']: float(row['total'])
        for row in payment_qs.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
                             .values('created_at__date').annotate(total=Sum('amount'))
    }
    days = [date_from + timezone.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    return JsonResponse({
        'labels': [d.strftime('%b %d') for d in days],
        'data': [totals.get(d, 0) for d in days],
    })

@staff_module_required('restaurant_kitchen')
def restaurant_revenue_trend(request):
    date_from, date_to = _report_range(request, default_days=6)
    qs = Payment.objects.filter(order__isnull=False, order__room_booking__isnull=True, order__bar_tab__isnull=True)
    return _trend_payload(qs, date_from, date_to)

@staff_module_required('bar')
def bar_revenue_trend(request):
    date_from, date_to = _report_range(request, default_days=6)
    return _trend_payload(Payment.objects.filter(order__bar_tab__isnull=False), date_from, date_to)
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


################################
### store Views start
################################

@staff_module_required('store')
def store_stock_items_json(request):
    department = request.GET.get('department', '')
    items = StockItem.objects.filter(department=department, is_active=True).order_by('name')
    data = [{'id': i.id, 'label': f"{i.name} ({i.quantity_on_hand:g} {i.get_unit_display()} in stock)"} for i in items]
    return JsonResponse({'items': data})

@staff_module_required('store')
def store_view(request):
    today = timezone.localdate()
    week_ago = today - timezone.timedelta(days=6)

    if request.method == 'POST':
        form = PurchaseLogForm(request.POST, department=request.POST.get('department'))
        if form.is_valid():
            data = form.cleaned_data
            if data['mode'] == 'existing':
                stock_item = data['stock_item']
            else:
                stock_item = StockItem.objects.create(
                    name=data['new_item_name'], 
                    department=data['department'],
                    unit=data['new_item_unit'], 
                    reorder_level=data.get('reorder_level') or 0,
                )
            purchase = PurchaseLog.objects.create(
                department=data['department'], 
                stock_item=stock_item, 
                quantity=data['quantity'],
                unit_cost=data['unit_cost'], 
                supplier=data.get('supplier'),
                vat_inclusive=data.get('vat_inclusive', True),
                notes=data.get('notes', ''), 
                purchased_by=request.user, 
                purchased_at=data['purchased_at'],
            )
            for f in request.FILES.getlist('receipts'):
                PurchaseReceipt.objects.create(purchase=purchase, image=f)
            messages.success(request, f"Purchase logged: {stock_item.name} — KSh {purchase.total_cost:,.0f}")
            return redirect('dashboard:store')
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = PurchaseLogForm(initial={'purchased_at': today, 'vat_inclusive': True, 'department': 'kitchen'})

    # NEW
    def _spend_between(d_from, d_to):
        qs = PurchaseLog.objects.filter(purchased_at__date__gte=d_from, purchased_at__date__lte=d_to)
        return sum((p.total_cost for p in qs), Decimal('0'))

    def _pct_change(current, previous):
        if not previous:
            return None
        return float((current - previous) / previous * 100)

    yesterday = today - timezone.timedelta(days=1)
    week_start = today - timezone.timedelta(days=6)
    last_week_start = week_start - timezone.timedelta(days=7)
    last_week_end = week_start - timezone.timedelta(days=1)
    month_start = today.replace(day=1)

    today_total = _spend_between(today, today)
    yesterday_total = _spend_between(yesterday, yesterday)
    week_total = _spend_between(week_start, today)
    last_week_total = _spend_between(last_week_start, last_week_end)
    month_total = _spend_between(month_start, today)

    today_change = _pct_change(today_total, yesterday_total)
    week_change = _pct_change(week_total, last_week_total)

    stock_value = sum(
        (i.quantity_on_hand * i.last_unit_cost for i in StockItem.objects.filter(is_active=True) if i.last_unit_cost),
        Decimal('0')
    )

    month_purchases = list(PurchaseLog.objects.filter(
        purchased_at__date__gte=month_start, purchased_at__date__lte=today
    ).select_related('supplier'))
    by_dept, by_supplier = {}, {}
    for p in month_purchases:
        by_dept[p.get_department_display()] = by_dept.get(p.get_department_display(), Decimal('0')) + p.total_cost
        key = p.supplier.name if p.supplier else "No supplier recorded"
        by_supplier[key] = by_supplier.get(key, Decimal('0')) + p.total_cost

    dept_max = max(by_dept.values(), default=Decimal('1')) or Decimal('1')
    top_departments = [
        {'label': n, 'amount': a, 'pct': float(a / dept_max * 100)}
        for n, a in sorted(by_dept.items(), key=lambda x: x[1], reverse=True)[:5]
    ]
    supplier_max = max(by_supplier.values(), default=Decimal('1')) or Decimal('1')
    top_suppliers = [
        {'label': n, 'amount': a, 'pct': float(a / supplier_max * 100)}
        for n, a in sorted(by_supplier.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    recent_purchases = PurchaseLog.objects.select_related('stock_item', 'purchased_by').prefetch_related('receipts')[:15]
    low_stock_items = [i for i in StockItem.objects.filter(is_active=True) if i.stock_status != 'ok']

    return render(request, 'dashboard/store.html', {
        'form': form,
        'today_total': today_total, 'yesterday_total': yesterday_total, 'today_change': today_change,
        'week_total': week_total, 'last_week_total': last_week_total, 'week_change': week_change,
        'month_total': month_total, 'stock_value': stock_value,
        'top_departments': top_departments, 'top_suppliers': top_suppliers,
        'recent_purchases': recent_purchases,
        'low_stock_items': low_stock_items,
        'department_choices': StockItem.DEPARTMENT_CHOICES,
        'suppliers': Supplier.objects.filter(is_active=True),
        'spend_trend_url': reverse('dashboard:store_spend_trend'),
    })

@staff_module_required('store')
def store_spend_trend(request):
    date_from, date_to = _report_range(request, default_days=13)
    days = [(date_from + timezone.timedelta(days=i)) for i in range((date_to - date_from).days + 1)]
    dept_codes = [c for c, _ in StockItem.DEPARTMENT_CHOICES]
    dept_labels = dict(StockItem.DEPARTMENT_CHOICES)

    purchases = list(PurchaseLog.objects.filter(purchased_at__date__gte=date_from, purchased_at__date__lte=date_to))
    daily = {d: {c: Decimal('0') for c in dept_codes} for d in days}
    for p in purchases:
        d = p.purchased_at.date() if hasattr(p.purchased_at, 'date') else p.purchased_at
        if d in daily and p.department in daily[d]:
            daily[d][p.department] += p.total_cost

    return JsonResponse({
        'labels': [d.strftime('%b %d') for d in days],
        'datasets': [
            {'label': dept_labels[c], 'data': [float(daily[d][c]) for d in days]}
            for c in dept_codes
        ],
    })
# NEW
@staff_module_required('store')
def store_usage_logs_view(request):
    DEPARTMENTS = [('kitchen', 'Kitchen'), ('housekeeping', 'Housekeeping'), ('bar', 'Bar')]
    valid_depts = {code for code, _ in DEPARTMENTS}

    dept = request.GET.get('department', 'kitchen')
    if dept not in valid_depts:
        dept = 'kitchen'  # guard: an unrecognized value would build a bad url name below

    preset = request.GET.get('preset', 'week')
    today = timezone.localdate()
    days = {'today': 0, 'week': 7, 'month': 30, 'quarter': 90, 'year': 365}.get(preset, 7)
    date_from = today - timezone.timedelta(days=days)

    usage_items = DailyUsageItem.objects.filter(
        log__department=dept, log__date__gte=date_from, log__date__lte=today
    ).select_related('stock_item', 'added_by', 'log').order_by('-log__date', '-added_at')

    if dept == 'bar':
        wastage_items = WastageLog.objects.filter(
            stock_item__department='bar', logged_at__date__gte=date_from, logged_at__date__lte=today
        ).select_related('stock_item', 'logged_by').order_by('-logged_at')
    else:
        wastage_items = WastageLog.objects.none()

    # Value-based trend, not raw quantity, this is what makes units comparable.
    chart_days = [(date_from + timezone.timedelta(days=i)) for i in range((today - date_from).days + 1)]
    daily_values = []
    for day in chart_days:
        day_value = 0
        for i in usage_items:
            if i.log.date != day:
                continue
            unit_cost = float(i.stock_item.last_unit_cost) if i.stock_item else float(i.custom_unit_cost or 0)
            day_value += float(i.quantity) * unit_cost
        daily_values.append(round(day_value, 2))

    total_value_issued = sum(
        (float(i.quantity) * (float(i.stock_item.last_unit_cost) if i.stock_item else float(i.custom_unit_cost or 0)))
        for i in usage_items
    )

    today_log, _ = DailyUsageLog.objects.get_or_create(department=dept, date=today)
    today_items = today_log.items.select_related('stock_item', 'added_by').order_by('-added_at')
    stock_items = StockItem.objects.filter(department=dept, is_active=True)

    return render(request, 'dashboard/store_usage_logs.html', {
        'usage_items': usage_items, 'wastage_items': wastage_items,
        'department': dept, 'preset': preset, 'date_from': date_from, 'date_to': today,
        'departments': DEPARTMENTS,
        'add_url_name': f'dashboard:{dept}_usage_log_add',
        'confirm_url_name': f'dashboard:{dept}_usage_log_confirm',
        'chart_labels': json.dumps([d.strftime('%b %d') for d in chart_days]),
        'chart_data': json.dumps(daily_values),
        'total_value_issued': round(total_value_issued, 2),
        'today_items': today_items, 'stock_items': stock_items,
        'unlocked_count': today_items.filter(is_locked=False).count(),
    })

@staff_module_required('store')
def store_purchase_detail(request, purchase_id):
    purchase = get_object_or_404(PurchaseLog.objects.select_related('stock_item', 'purchased_by').prefetch_related('receipts'), id=purchase_id)
    return render(request, 'dashboard/store_purchase_detail.html', {'purchase': purchase})

@staff_module_required('store')
def store_expenditure_report(request):
    preset = request.GET.get('preset', 'month')
    today = timezone.localdate()
    if request.GET.get('date_from') and request.GET.get('date_to'):
        date_from = datetime.strptime(request.GET['date_from'], '%Y-%m-%d').date()
        date_to = datetime.strptime(request.GET['date_to'], '%Y-%m-%d').date()
    else:
        days = {'today': 0, 'week': 7, 'month': 30, 'quarter': 90, 'half_year': 182, 'year': 365}.get(preset, 30)
        date_from, date_to = today - timezone.timedelta(days=days), today

    purchases = get_purchases_in_range(date_from, date_to)
    summary = summarize_by_department(purchases)
    grand_incl = sum((d['total_incl'] for d in summary.values()), Decimal('0'))
    grand_excl = sum((d['total_excl'] for d in summary.values()), Decimal('0'))
    grand_vat = sum((d['total_vat'] for d in summary.values()), Decimal('0'))

    span = (date_to - date_from).days + 1
    daily_average = grand_incl / span if span else Decimal('0')

    # Same-length prior period, for a "vs last period" comparison
    prev_to = date_from - timezone.timedelta(days=1)
    prev_from = prev_to - timezone.timedelta(days=span - 1)
    prev_total = sum((p.total_cost for p in get_purchases_in_range(prev_from, prev_to)), Decimal('0'))
    change_pct = float((grand_incl - prev_total) / prev_total * 100) if prev_total else None

    dept_max = max((d['total_incl'] for d in summary.values()), default=Decimal('1')) or Decimal('1')
    for d in summary.values():
        d['pct'] = float(d['total_incl'] / dept_max * 100)

    return render(request, 'dashboard/store_expenditure.html', {
        'summary': summary, 'date_from': date_from, 'date_to': date_to, 'preset': preset,
        'grand_incl': grand_incl, 'grand_excl': grand_excl, 'grand_vat': grand_vat,
        'daily_average': daily_average, 'change_pct': change_pct,
    })

@staff_module_required('store')
def store_expenditure_pdf(request):
    today = timezone.localdate()
    default_from = today.replace(day=1)

    date_from = _parse_date_input(request.GET.get('date_from'), default_from)
    date_to = _parse_date_input(request.GET.get('date_to'), today)

    purchases = get_purchases_in_range(date_from, date_to)
    summary = summarize_by_department(purchases)

    grand_incl = sum((d['total_incl'] for d in summary.values()), Decimal('0'))
    grand_excl = sum((d['total_excl'] for d in summary.values()), Decimal('0'))
    grand_vat = sum((d['total_vat'] for d in summary.values()), Decimal('0'))

    html_string = render_to_string('dashboard/store_expenditure_pdf.html', {
        'summary': summary,
        'date_from': date_from,
        'date_to': date_to,
        'generated_at': timezone.now(),
        'generated_by': request.user.get_full_name() or request.user.username,
        'grand_incl': grand_incl,
        'grand_excl': grand_excl,
        'grand_vat': grand_vat,
    })

    pdf_file = HTML(
        string=html_string,
        base_url=request.build_absolute_uri('/')
    ).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="expenditure_{date_from}_to_{date_to}.pdf"'
    return response

@staff_module_required('store')
def store_inventory_view(request):
    department_filter = request.GET.get('department', '')
    items = StockItem.objects.filter(is_active=True)
    if department_filter:
        items = items.filter(department=department_filter)

    items = list(items.order_by('department', 'name'))

    chart_labels = [i.name for i in items]
    chart_levels = [float(i.quantity_on_hand) for i in items]
    chart_reorder = [float(i.reorder_level) for i in items]
    chart_colors = [
        '#dc2626' if i.stock_status == 'danger' else '#C9A227' if i.stock_status == 'warning' else '#0B6B3A'
        for i in items
    ]

    return render(request, 'dashboard/store_inventory.html', {
        'items': items,
        'department_filter': department_filter,
        'department_choices': StockItem.DEPARTMENT_CHOICES,
        'chart_labels': json.dumps(chart_labels),
        'chart_levels': json.dumps(chart_levels),
        'chart_reorder': json.dumps(chart_reorder),
        'chart_colors': json.dumps(chart_colors),
    })

@staff_module_required('store')
def store_supplier_comparison(request, stock_item_id):
    stock_item = get_object_or_404(StockItem, id=stock_item_id)
    purchases = stock_item.purchase_logs.select_related('supplier').order_by('-purchased_at')

    by_supplier = {}
    for p in purchases:
        key = p.supplier.name if p.supplier else "No supplier recorded"
        entry = by_supplier.setdefault(key, {'prices': [], 'last_date': p.purchased_at})
        entry['prices'].append(p.unit_cost)
        if p.purchased_at > entry['last_date']:
            entry['last_date'] = p.purchased_at

    rows = []
    for name, data in by_supplier.items():
        rows.append({
            'name': name, 'min_price': min(data['prices']), 'avg_price': sum(data['prices']) / len(data['prices']),
            'last_price': data['prices'][0], 'purchase_count': len(data['prices']), 'last_date': data['last_date'],
        })
    rows.sort(key=lambda r: r['avg_price'])

    return render(request, 'dashboard/store_supplier_comparison.html', {'stock_item': stock_item, 'rows': rows})

@staff_module_required('store')
def stocktake_list(request):
    takes = StockTake.objects.select_related('conducted_by', 'finalized_by').order_by('-date', '-created_at')
    return render(request, 'dashboard/stocktake_list.html', {'takes': takes})

@staff_module_required('store')
def stocktake_start(request):
    if request.method != 'POST':
        return redirect('dashboard:stocktake_list')
    department = request.POST.get('department')
    stock_take = StockTake.objects.create(department=department, conducted_by=request.user)
    for item in StockItem.objects.filter(department=department, is_active=True):
        StockTakeLine.objects.create(stock_take=stock_take, stock_item=item, system_quantity=item.quantity_on_hand)
    return redirect('dashboard:stocktake_detail', stocktake_id=stock_take.id)

@staff_module_required('store')
def stocktake_detail(request, stocktake_id):
    stock_take = get_object_or_404(StockTake, id=stocktake_id)
    lines = stock_take.lines.select_related('stock_item').order_by('stock_item__name')

    if request.method == 'POST' and stock_take.status == 'draft':
        for line in lines:
            value = request.POST.get(f'counted_{line.id}', '').strip()
            note = request.POST.get(f'notes_{line.id}', '').strip()
            if value:
                line.counted_quantity = Decimal(value)
                line.notes = note
                line.save(update_fields=['counted_quantity', 'notes'])
        messages.success(request, "Counts saved. Finalize once every item is counted.")
        return redirect('dashboard:stocktake_detail', stocktake_id=stock_take.id)

    return render(request, 'dashboard/stocktake_detail.html', {'stock_take': stock_take, 'lines': lines})

@staff_module_required('store')
def stocktake_finalize(request, stocktake_id):
    if request.method != 'POST':
        return redirect('dashboard:stocktake_detail', stocktake_id=stocktake_id)
    stock_take = get_object_or_404(StockTake, id=stocktake_id, status='draft')

    uncounted = stock_take.lines.filter(counted_quantity__isnull=True)
    if uncounted.exists():
        messages.error(request, f"{uncounted.count()} item(s) still uncounted. Every item must have a count before finalizing.")
        return redirect('dashboard:stocktake_detail', stocktake_id=stock_take.id)

    with transaction.atomic():
        for line in stock_take.lines.select_related('stock_item'):
            line.stock_item.quantity_on_hand = line.counted_quantity
            line.stock_item.save(update_fields=['quantity_on_hand'])
            notify_low_stock_if_needed(line.stock_item)
        stock_take.status = 'finalized'
        stock_take.finalized_by = request.user
        stock_take.finalized_at = timezone.now()
        stock_take.save(update_fields=['status', 'finalized_by', 'finalized_at'])

    messages.success(request, "Stock take finalized. System quantities now match the physical count.")
    return redirect('dashboard:stocktake_detail', stocktake_id=stock_take.id)

### end of store views

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
    
# ==============================================================================
# HRM Views
# ==============================================================================

from django.core.paginator import Paginator

def _qs_without(request, *keys):
    qd = request.GET.copy()
    for k in keys:
        qd.pop(k, None)
    return qd.urlencode()

@login_required
def hrm_home(request):
    today = timezone.localdate()
    attendance_today = AttendanceRecord.objects.filter(staff=request.user, date=today).first()
    upcoming_shifts = ShiftAssignment.objects.filter(staff=request.user, date__gte=today).select_related('shift').order_by('date')[:7]
    pending_leave = LeaveRequest.objects.filter(staff=request.user, status='pending').count()
    return render(request, 'dashboard/hrm_home.html', {
        'attendance_today': attendance_today, 'upcoming_shifts': upcoming_shifts,
        'pending_leave': pending_leave, 'can_manage': can_manage_hrm(request.user),
    })

import math
from django.conf import settings

# Alias settings to match the names used in your view context
GEOFENCE_LAT = settings.HOTEL_LATITUDE
GEOFENCE_LNG = settings.HOTEL_LONGITUDE
GEOFENCE_RADIUS_M = settings.HOTEL_GEOFENCE_RADIUS_METERS

def is_within_geofence(lat, lng, center_lat=GEOFENCE_LAT, center_lng=GEOFENCE_LNG, radius_m=GEOFENCE_RADIUS_M):
    """
    Calculates distance between (lat, lng) and the geofence center using the Haversine formula.
    Returns True if distance <= radius_m in meters.
    """
    if lat is None or lng is None:
        return False

    R = 6371000  # Earth's radius in meters
    phi1, phi2 = math.radians(lat), math.radians(center_lat)
    dphi = math.radians(center_lat - lat)
    dlambda = math.radians(center_lng - lng)

    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c

    return distance <= radius_m

@login_required
def hrm_clock_view(request):
    today = timezone.localdate()
    record, _ = AttendanceRecord.objects.get_or_create(staff=request.user, date=today)
    assignment = ShiftAssignment.objects.filter(staff=request.user, date=today).select_related('shift').first()
    form = ClockVerifyForm(staff=request.user)
    return render(request, 'dashboard/hrm_clock.html', {
        'record': record, 'assignment': assignment, 'form': form,
        'geofence_lat': GEOFENCE_LAT, 'geofence_lng': GEOFENCE_LNG, 'geofence_radius_m': GEOFENCE_RADIUS_M,
    })

@login_required
def hrm_clock_action(request, action):
    if request.method != 'POST' or action not in ('in', 'out'):
        return redirect('dashboard:hrm_clock')
    today = timezone.localdate()
    record, _ = AttendanceRecord.objects.get_or_create(staff=request.user, date=today)

    form = ClockVerifyForm(request.POST, staff=request.user)
    if not form.is_valid():
        for error_list in form.errors.values():
            for error in error_list:
                messages.error(request, error)
        return redirect('dashboard:hrm_clock')

    lat, lng = form.cleaned_data['lat'], form.cleaned_data['lng']
    phone = form.cleaned_data['phone'].strip()
    within = is_within_geofence(lat, lng)

    # Anti buddy-punching: this exact phone number can't already have been
    # used for this same action (clock-in or clock-out) by someone else today.
    phone_field = f'clock_{action}_phone'
    already_used = AttendanceRecord.objects.filter(
        date=today, **{phone_field: phone}
    ).exclude(staff=request.user).exists()
    if already_used:
        messages.error(
            request,
            f"This phone number has already been used to clock {'in' if action == 'in' else 'out'} "
            "today by another staff member. Contact HR if this is a mistake."
        )
        return redirect('dashboard:hrm_clock')

    if action == 'in':
        if record.clock_in_time:
            messages.error(request, "You've already clocked in today.")
            return redirect('dashboard:hrm_clock')
        record.clock_in_time = timezone.now()
        record.clock_in_lat, record.clock_in_lng, record.clock_in_phone = lat, lng, phone
        record.clock_in_within_geofence = within

        assignment = ShiftAssignment.objects.filter(staff=request.user, date=today).select_related('shift').first()
        if assignment:
            record.shift_assignment = assignment
            shift_start = timezone.make_aware(datetime.combine(today, assignment.shift.start_time))
            grace_end = shift_start + timedelta(minutes=assignment.shift.grace_minutes)
            if record.clock_in_time > grace_end:
                record.is_late = True
                record.late_minutes = int((record.clock_in_time - shift_start).total_seconds() // 60)
        record.save()
        msg = "Clocked in."
        if not within:
            msg += " Note: outside the hotel geofence, flagged for review."
        messages.success(request, msg)
    else:
        if not record.clock_in_time:
            messages.error(request, "You haven't clocked in yet today.")
            return redirect('dashboard:hrm_clock')
        if record.clock_out_time:
            messages.error(request, "You've already clocked out today.")
            return redirect('dashboard:hrm_clock')
        record.clock_out_time = timezone.now()
        record.clock_out_lat, record.clock_out_lng, record.clock_out_phone = lat, lng, phone
        record.clock_out_within_geofence = within
        record.save()
        msg = "Clocked out."
        if not within:
            msg += " Note: outside the hotel geofence, flagged for review."
        messages.success(request, msg)

    return redirect('dashboard:hrm_clock')

@login_required
def hrm_my_attendance(request):
    user = request.user
    base_qs = AttendanceRecord.objects.filter(staff=user).order_by('-date')
    
    # Query parameters
    time_frame = request.GET.get('time_frame', 'all')
    status_filter = request.GET.get('status', 'all')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    page_number = request.GET.get('page', 1)

    today = timezone.localdate()
    
    # Time-frame pre-filtering
    if time_frame == 'this_week':
        start_of_week = today - timedelta(days=today.weekday())
        base_qs = base_qs.filter(date__gte=start_of_week)
    elif time_frame == 'last_week':
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        base_qs = base_qs.filter(date__range=[start_of_last_week, end_of_last_week])
    elif time_frame == 'this_month':
        base_qs = base_qs.filter(date__year=today.year, date__month=today.month)

    # Date range inputs
    if start_date:
        base_qs = base_qs.filter(date__gte=start_date)
    if end_date:
        base_qs = base_qs.filter(date__lte=end_date)

    # Pre-compute metrics on filtered base query
    total_records_count = base_qs.count()
    total_late_count = base_qs.filter(is_late=True).count()
    total_flagged_count = base_qs.filter(clock_in_time__isnull=False, clock_in_within_geofence=False).count()
    
    on_time_count = total_records_count - total_late_count
    on_time_rate = round((on_time_count / total_records_count) * 100) if total_records_count > 0 else 100

    # Status tab filter (applied to the table list)
    table_qs = base_qs
    if status_filter == 'late':
        table_qs = table_qs.filter(is_late=True)
    elif status_filter == 'flagged':
        table_qs = table_qs.filter(clock_in_time__isnull=False, clock_in_within_geofence=False)

    # Pagination (15 records per page)
    paginator = Paginator(table_qs, 15)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'paginator': paginator,
        'total_records_count': total_records_count,
        'total_late_count': total_late_count,
        'total_flagged_count': total_flagged_count,
        'on_time_rate': on_time_rate,
        'time_frame': time_frame,
        'status_filter': status_filter,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'dashboard/hrm_my_attendance.html', context)

def _compute_shift_display(a, attendance_by_date, leave_by_date, off_weekdays, today):
    lr = leave_by_date.get(a.date)
    if lr:
        return 'on_leave', 'On Leave', lr.leave_type.name
    if a.status == 'cancelled':
        return 'cancelled', 'Cancelled', ''
    if a.date.weekday() in off_weekdays:
        return 'weekly_off', 'Weekly Off', ''
    if a.date >= today:
        return 'scheduled', 'Scheduled', ''
    record = attendance_by_date.get(a.date)
    if record and record.clock_in_time:
        detail = f"Late by {record.late_minutes}m" if record.is_late else ''
        return 'completed', 'Completed', detail
    return 'absent', 'Absent', ''


@login_required
def hrm_my_shifts(request):
    today = timezone.localdate()
    now_time = timezone.localtime().time()

    time_frame = request.GET.get('time_frame', 'all')
    status_filter = request.GET.get('status', 'all')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    assignments_qs = ShiftAssignment.objects.filter(staff=request.user).select_related(
        'shift', 'previous_shift', 'swap_partner'
    )

    if start_date and end_date:
        assignments_qs = assignments_qs.filter(date__range=[start_date, end_date])
    elif time_frame == 'this_week':
        start_of_week = today - timedelta(days=today.weekday())
        assignments_qs = assignments_qs.filter(date__range=[start_of_week, start_of_week + timedelta(days=6)])
    elif time_frame == 'last_week':
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        assignments_qs = assignments_qs.filter(date__range=[start_of_last_week, start_of_last_week + timedelta(days=6)])
    elif time_frame == 'this_month':
        assignments_qs = assignments_qs.filter(date__year=today.year, date__month=today.month)
    elif time_frame == 'upcoming':
        assignments_qs = assignments_qs.filter(date__gte=today)

    assignments_qs = assignments_qs.order_by('-date', '-shift__start_time')
    all_items = list(assignments_qs)

    today_attendance = AttendanceRecord.objects.filter(staff=request.user, date=today).first()
    is_currently_clocked_in = bool(today_attendance and today_attendance.clock_in_time and not today_attendance.clock_out_time)

    all_dates = {a.date for a in all_items}
    attendance_by_date = {
        rec.date: rec for rec in AttendanceRecord.objects.filter(staff=request.user, date__in=all_dates)
    }
    leave_by_date = {}
    if all_dates:
        for lr in LeaveRequest.objects.filter(
            staff=request.user, status='approved',
            end_date__gte=min(all_dates), start_date__lte=max(all_dates)
        ).select_related('leave_type'):
            d = lr.start_date
            while d <= lr.end_date:
                if d in all_dates:
                    leave_by_date[d] = lr
                d += timedelta(days=1)
    weekly_off = StaffWeeklyOff.objects.filter(staff=request.user).first()
    off_weekdays = set(weekly_off.weekdays) if weekly_off else set()

    for a in all_items:
        a.display_status, a.display_label, a.display_detail = _compute_shift_display(
            a, attendance_by_date, leave_by_date, off_weekdays, today
        )
        a.is_swapped = bool(a.previous_shift_id or a.swap_partner_id)

    if status_filter == 'on_shift':
        all_items = [a for a in all_items if a.date == today and a.display_status not in ('cancelled', 'on_leave')]
    elif status_filter == 'scheduled':
        all_items = [a for a in all_items if a.display_status == 'scheduled']
    elif status_filter == 'completed':
        all_items = [a for a in all_items if a.display_status == 'completed']
    elif status_filter == 'absent':
        all_items = [a for a in all_items if a.display_status == 'absent']
    elif status_filter == 'on_leave':
        all_items = [a for a in all_items if a.display_status == 'on_leave']
    elif status_filter == 'swapped':
        all_items = [a for a in all_items if a.is_swapped]
    elif status_filter == 'cancelled':
        all_items = [a for a in all_items if a.display_status == 'cancelled']

    paginator = Paginator(all_items, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    my_swaps = ShiftSwapRequest.objects.filter(requested_by=request.user).order_by('-created_at')[:10]

    context = {
        'page_obj': page_obj, 'paginator': paginator, 'my_swaps': my_swaps,
        'today': today, 'now_time': now_time, 'is_currently_clocked_in': is_currently_clocked_in,
        'time_frame': time_frame, 'status_filter': status_filter,
        'start_date': start_date, 'end_date': end_date, 'total_shifts': len(all_items),
    }
    return render(request, 'dashboard/hrm_my_shifts.html', context)

@login_required
def hrm_request_swap(request):
    if request.method == 'POST':
        form = ShiftSwapRequestForm(request.POST, staff=request.user)
        if form.is_valid():
            swap = form.save(commit=False)
            swap.requested_by = request.user
            try:
                swap.full_clean()
                swap.save()
                messages.success(request, "Swap request submitted for manager review.")
                return redirect('dashboard:hrm_my_shifts')
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
    else:
        form = ShiftSwapRequestForm(staff=request.user)
    return render(request, 'dashboard/hrm_request_swap.html', {'form': form})

@login_required
def hrm_my_leave(request):
    today = timezone.localdate()
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST, staff=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Leave request submitted.")
            return redirect('dashboard:hrm_my_leave')
    else:
        form = LeaveRequestForm(staff=request.user)

    requests_qs = LeaveRequest.objects.filter(staff=request.user).select_related('leave_type').order_by('-created_at')
    balances = LeaveBalance.objects.filter(staff=request.user, year=today.year).select_related('leave_type')
    balances_map = {b.leave_type_id: b.remaining_days for b in balances}
    active_leave = LeaveRequest.objects.filter(
        staff=request.user, status='approved', start_date__lte=today, end_date__gte=today
    ).select_related('leave_type').first()
    report_back = (active_leave.end_date + timedelta(days=1)) if active_leave else None

    return render(request, 'dashboard/hrm_my_leave.html', {
        'form': form, 'requests': requests_qs, 'balances': balances, 'balances_map': balances_map,
        'active_leave': active_leave, 'report_back': report_back,
    })

import calendar

@login_required
def hrm_my_payslips(request):
    selected_year = request.GET.get('year', '')
    selected_month = request.GET.get('month', '')

    records = PayrollRecord.objects.filter(staff=request.user, status='finalized')

    # Apply filters
    if selected_year:
        records = records.filter(period_year=selected_year)
    if selected_month:
        records = records.filter(period_month=selected_month)

    records = records.order_by('-period_year', '-period_month')

    # Compute calculations per instance
    for record in records:
        if isinstance(record.period_month, int) or (isinstance(record.period_month, str) and str(record.period_month).isdigit()):
            record.period_month_name = calendar.month_name[int(record.period_month)]
        else:
            record.period_month_name = record.period_month
            
        overtime_pay = (record.overtime_hours or 0) * (record.overtime_rate or 0)
        record.calculated_gross = (record.basic_salary or 0) + (record.allowances_total or 0) + overtime_pay
        record.calculated_net = record.calculated_gross - (record.deductions_total or 0)

    # Summary Stats & YTD Aggregation
    total_payslips_count = records.count()
    current_year = timezone.now().year
    
    ytd_records = PayrollRecord.objects.filter(
        staff=request.user, 
        status='finalized', 
        period_year=current_year
    ).annotate(
        net_calculated=ExpressionWrapper(
            F('basic_salary') + F('allowances_total') + (F('overtime_hours') * F('overtime_rate')) - F('deductions_total'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )
    )
    ytd_net_pay = ytd_records.aggregate(total=Sum('net_calculated'))['total'] or 0.0

    # Distinct years dropdown
    # Fetch only years that have actual finalized records for this staff member
    available_years = list(
        PayrollRecord.objects.filter(staff=request.user, status='finalized')
        .values_list('period_year', flat=True)
        .distinct()
        .order_by('-period_year')
    )

    # Fallback: If it's a new employee with no finalized records yet, default to the current year
    if not available_years:
        available_years = [timezone.now().year]

    context = {
        'records': records,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'total_payslips_count': total_payslips_count,
        'ytd_net_pay': ytd_net_pay,
        'available_years': available_years,
    }
    return render(request, 'dashboard/hrm_my_payslips.html', context)

def _prepare_record_calculations(record):
    """Helper method to ensure monthly calculations exist on the instance."""
    if isinstance(record.period_month, int) or (isinstance(record.period_month, str) and str(record.period_month).isdigit()):
        record.period_month_name = calendar.month_name[int(record.period_month)]
    else:
        record.period_month_name = record.period_month

    record.calculated_overtime = (record.overtime_hours or 0) * (record.overtime_rate or 0)
    record.calculated_gross = (record.basic_salary or 0) + (record.allowances_total or 0) + record.calculated_overtime
    record.calculated_net = record.calculated_gross - (record.deductions_total or 0)
    return record

@login_required
def hrm_payslip_pdf(request, record_id):
    """Single payslip PDF view."""
    record = get_object_or_404(PayrollRecord, id=record_id, staff=request.user, status='finalized')
    record = _prepare_record_calculations(record)

    html_string = render_to_string('dashboard/hrm_payslip_pdf.html', {
        'records': [record],  # Pass as a 1-item list to reuse the template loop
        'generated_at': timezone.now()
    })
    
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="payslip_{record.period_month}_{record.period_year}.pdf"'
    return response

@login_required
def hrm_payslips_bulk_download(request):
    """Bulk range payslip PDF bundle export view."""
    if request.method == 'POST':
        try:
            months_count = int(request.POST.get('months_range', 3))
        except ValueError:
            months_count = 3

        records_qs = PayrollRecord.objects.filter(
            staff=request.user, 
            status='finalized'
        ).order_by('-period_year', '-period_month')[:months_count]

        if not records_qs.exists():
            messages.error(request, "No finalized payslips found for the selected date range.")
            return redirect('dashboard:hrm_my_payslips')

        records = [_prepare_record_calculations(r) for r in records_qs]

        html_string = render_to_string('dashboard/hrm_payslip_pdf.html', {
            'records': records,
            'generated_at': timezone.now()
        })
        
        pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="payslips_last_{months_count}_months.pdf"'
        return response

    return redirect('dashboard:hrm_my_payslips')

@login_required
def hrm_my_reviews(request):
    reviews = PerformanceReview.objects.filter(staff=request.user, status='finalized').order_by('-created_at')
    return render(request, 'dashboard/hrm_my_reviews.html', {'reviews': reviews})

# ---------- Management tier ----------

@hrm_manager_required
def hrm_staff_list(request):
    query = request.GET.get('q', '').strip()
    dept = request.GET.get('department', '')
    status = request.GET.get('status', '')

    users_qs = User.objects.filter(is_staff=True).select_related('staff_profile').prefetch_related('groups')
    if query:
        users_qs = users_qs.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(username__icontains=query) | Q(staff_profile__national_id__icontains=query)
        )
    if dept:
        users_qs = users_qs.filter(staff_profile__department=dept)
    if status:
        users_qs = users_qs.filter(staff_profile__employment_status=status)

    staff_list = sorted(users_qs, key=lambda u: (get_role_rank(u), (u.first_name or u.username).lower()))
    page_obj = Paginator(staff_list, 12).get_page(request.GET.get('page', 1))

    return render(request, 'dashboard/hrm_staff_list.html', {
        'page_obj': page_obj, 'query': query, 'dept': dept, 'status': status,
        'department_options': StaffProfile.DEPARTMENT_CHOICES,
        'status_options': StaffProfile.EMPLOYMENT_STATUS_CHOICES,
        'total_staff': User.objects.filter(is_staff=True).count(),
    })

@hrm_manager_required
def hrm_staff_create(request):
    if request.method == 'POST':
        form = StaffHireForm(request.POST, requesting_user=request.user)
        if form.is_valid():
            user, profile = form.save(created_by=request.user)
            messages.success(request, f"{user.get_full_name() or user.username} has been hired and their login created.")
            return redirect('dashboard:hrm_staff_detail', user_id=user.id)
    else:
        form = StaffHireForm(requesting_user=request.user)
    return render(request, 'dashboard/hrm_staff_create.html', {'form': form})

@hrm_manager_required
def hrm_staff_detail(request, user_id):
    staff_user = get_object_or_404(User.objects.select_related('staff_profile').prefetch_related('groups'), id=user_id)
    profile = getattr(staff_user, 'staff_profile', None)
    attendance = AttendanceRecord.objects.filter(staff=staff_user).order_by('-date')[:30]
    leave_requests = LeaveRequest.objects.filter(staff=staff_user).select_related('leave_type').order_by('-created_at')[:10]
    balances = LeaveBalance.objects.filter(staff=staff_user, year=timezone.localdate().year).select_related('leave_type')
    payroll = PayrollRecord.objects.filter(staff=staff_user).order_by('-period_year', '-period_month')[:6]
    reviews = PerformanceReview.objects.filter(staff=staff_user).order_by('-created_at')[:5]
    history = EmploymentAction.objects.filter(staff=staff_user).select_related('performed_by')[:20]
    return render(request, 'dashboard/hrm_staff_detail.html', {
        'staff_user': staff_user, 'profile': profile, 'attendance': attendance,
        'leave_requests': leave_requests, 'balances': balances, 'payroll': payroll, 'reviews': reviews,
        'history': history, 'can_manage_target_user': can_manage_target(request.user, staff_user),
    })

@hrm_manager_required
def hrm_staff_role_update(request, user_id):
    staff_user = get_object_or_404(User.objects.select_related('staff_profile'), id=user_id)
    profile = getattr(staff_user, 'staff_profile', None)
    if not profile:
        messages.error(request, "This user has no staff profile yet.")
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)
    if not can_manage_target(request.user, staff_user):
        messages.error(request, "You don't have permission to change this staff member's role.")
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)

    old_rank = get_role_rank(staff_user)
    old_position, old_department = profile.position, profile.department

    if request.method == 'POST':
        form = StaffRoleUpdateForm(request.POST, requesting_user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            profile.position = data['position']
            profile.department = data['department']
            profile.department_note = data['department_note']
            profile.pay_type = data['pay_type']
            profile.pay_rate = data['pay_rate']
            profile.save()
            staff_user.groups.set(data['groups'])

            new_rank = get_role_rank(staff_user)
            action = 'promoted' if new_rank < old_rank else 'demoted' if new_rank > old_rank else 'updated'
            EmploymentAction.objects.create(
                staff=staff_user, action=action,
                previous_position=old_position, new_position=profile.position,
                previous_department=old_department, new_department=profile.department,
                note=data['note'], performed_by=request.user,
            )
            messages.success(request, f"{staff_user.get_full_name() or staff_user.username}'s role has been updated.")
            return redirect('dashboard:hrm_staff_detail', user_id=user_id)
    else:
        form = StaffRoleUpdateForm(requesting_user=request.user, initial={
            'position': profile.position, 'department': profile.department,
            'department_note': profile.department_note, 'pay_type': profile.pay_type,
            'pay_rate': profile.pay_rate, 'groups': staff_user.groups.all(),
        })
    return render(request, 'dashboard/hrm_staff_role_update.html', {'form': form, 'staff_user': staff_user})

@hrm_manager_required
def hrm_staff_status_action(request, user_id, action):
    if request.method != 'POST' or action not in ('suspend', 'terminate', 'reinstate'):
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)
    staff_user = get_object_or_404(User.objects.select_related('staff_profile'), id=user_id)
    profile = getattr(staff_user, 'staff_profile', None)
    if not profile:
        messages.error(request, "This user has no staff profile yet.")
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)
    if not can_manage_target(request.user, staff_user):
        messages.error(request, "You don't have permission to change this staff member's employment status.")
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)

    note = request.POST.get('note', '').strip()
    if action in ('suspend', 'terminate') and not note:
        messages.error(request, "Please provide a reason.")
        return redirect('dashboard:hrm_staff_detail', user_id=user_id)

    if action == 'suspend':
        profile.employment_status = 'suspended'
        staff_user.is_active = False
        log_action = 'suspended'
    elif action == 'terminate':
        profile.employment_status = 'terminated'
        profile.is_active_staff = False
        staff_user.is_active = False
        log_action = 'terminated'
    else:
        profile.employment_status = 'active'
        profile.is_active_staff = True
        staff_user.is_active = True
        log_action = 'reinstated'

    profile.save()
    staff_user.save(update_fields=['is_active'])
    EmploymentAction.objects.create(staff=staff_user, action=log_action, note=note, performed_by=request.user)
    messages.success(request, f"{staff_user.get_full_name() or staff_user.username} has been {log_action}.")
    return redirect('dashboard:hrm_staff_detail', user_id=user_id)

@hrm_manager_required
def hrm_attendance_report(request):
    today = timezone.localdate()
    days = int(request.GET.get('days', 30))
    date_from = today - timedelta(days=days - 1)
    dept = request.GET.get('dept', '')

    all_active_profiles = list(
        StaffProfile.objects.filter(employment_status__in=['active', 'on_leave']).select_related('user')
    )
    all_staff_ids = [p.user_id for p in all_active_profiles]

    # ============ Daily Roster + Comparative Chart (single day, navigable) ============
    roster_date_str = request.GET.get('roster_date', '')
    try:
        roster_date = datetime.strptime(roster_date_str, '%Y-%m-%d').date() if roster_date_str else today
    except ValueError:
        roster_date = today

    roster_status_filter = request.GET.get('roster_status', '')
    roster_q = request.GET.get('roster_q', '').strip()

    day_profiles = all_active_profiles
    if dept:
        day_profiles = [p for p in day_profiles if p.department == dept]
    if roster_q:
        ql = roster_q.lower()
        day_profiles = [p for p in day_profiles if ql in (p.user.get_full_name() or p.user.username).lower()]

    weekday = roster_date.weekday()
    day_ids = [p.user_id for p in day_profiles]
    off_staff_ids = set(
        StaffWeeklyOff.objects.filter(staff_id__in=day_ids, weekdays__contains=[weekday])
        .values_list('staff_id', flat=True)
    )
    leave_map = {
        lr.staff_id: lr for lr in LeaveRequest.objects.filter(
            staff_id__in=day_ids, status='approved', start_date__lte=roster_date, end_date__gte=roster_date
        ).select_related('leave_type')
    }
    assignment_map = {
        a.staff_id: a for a in ShiftAssignment.objects.filter(staff_id__in=day_ids, date=roster_date).select_related('shift')
    }
    attendance_map = {
        rec.staff_id: rec for rec in AttendanceRecord.objects.filter(staff_id__in=day_ids, date=roster_date)
    }

    now = timezone.now()
    all_rows = []
    for profile in day_profiles:
        uid = profile.user_id
        row = {'profile': profile, 'user': profile.user, 'shift': None, 'clock_in': None, 'clock_out': None, 'detail': '', 'late_minutes': 0}
        if uid in leave_map:
            row['status'], row['label'] = 'on_leave', 'On Leave'
            row['detail'] = leave_map[uid].leave_type.name
        elif uid in off_staff_ids:
            row['status'], row['label'] = 'weekly_off', 'Weekly Off'
        elif uid not in assignment_map:
            row['status'], row['label'] = 'not_scheduled', 'No Shift Scheduled'
        else:
            assignment = assignment_map[uid]
            record = attendance_map.get(uid)
            row['shift'] = assignment.shift
            if record and record.clock_in_time:
                row['clock_in'], row['clock_out'] = record.clock_in_time, record.clock_out_time
                if record.is_late:
                    row['status'], row['label'], row['late_minutes'] = 'late', f"Late ({record.late_minutes}m)", record.late_minutes
                else:
                    row['status'], row['label'] = 'on_time', 'On Time'
                if not record.clock_out_time:
                    row['detail'] = 'Currently on duty'
            else:
                shift_start = timezone.make_aware(datetime.combine(roster_date, assignment.shift.start_time))
                grace_end = shift_start + timedelta(minutes=assignment.shift.grace_minutes)
                if roster_date < today or (roster_date == today and now > grace_end):
                    row['status'], row['label'] = 'absent', 'Absent'
                else:
                    row['status'], row['label'] = 'not_due', 'Not Due Yet'
        all_rows.append(row)

    status_counts = {}
    for r in all_rows:
        status_counts[r['status']] = status_counts.get(r['status'], 0) + 1

    filtered_rows = [r for r in all_rows if not roster_status_filter or r['status'] == roster_status_filter]
    roster_page = Paginator(filtered_rows, 15).get_page(request.GET.get('roster_page', 1))
    roster_base_qs = _qs_without(request, 'roster_page')

    chart_payload = []
    for r in all_rows:
        value = max(r['late_minutes'], 5) if r['status'] == 'late' else 5
        chart_payload.append({
            'name': r['user'].get_full_name() or r['user'].username,
            'status': r['status'], 'label': r['label'], 'detail': r['detail'],
            'value': value,
            'clock_in': timezone.localtime(r['clock_in']).strftime('%H:%M') if r['clock_in'] else None,
            'clock_out': timezone.localtime(r['clock_out']).strftime('%H:%M') if r['clock_out'] else None,
        })
    chart_payload.sort(key=lambda c: c['name'])
    chart_height = max(220, len(chart_payload) * 36)

    # ============ Range summary (totals across `days`, still useful for trends) ============
    chart_days = [(date_from + timedelta(days=i)) for i in range((today - date_from).days + 1)]
    weekoff_by_staff = {off.staff_id: set(off.weekdays) for off in StaffWeeklyOff.objects.filter(staff_id__in=all_staff_ids)}
    leaves_by_staff = {}
    for lr in LeaveRequest.objects.filter(staff_id__in=all_staff_ids, status='approved', end_date__gte=date_from, start_date__lte=today):
        leaves_by_staff.setdefault(lr.staff_id, []).append((lr.start_date, lr.end_date))
    assignments_by_day = {}
    for a in ShiftAssignment.objects.filter(staff_id__in=all_staff_ids, date__gte=date_from, date__lte=today).select_related('shift'):
        assignments_by_day.setdefault(a.date, {})[a.staff_id] = a
    attendance_by_day = {}
    for rec in AttendanceRecord.objects.filter(staff_id__in=all_staff_ids, date__gte=date_from, date__lte=today):
        attendance_by_day.setdefault(rec.date, {})[rec.staff_id] = rec

    emp_totals = {uid: {'on_time': 0, 'late': 0, 'absent': 0, 'off_leave': 0} for uid in all_staff_ids}
    for day in chart_days:
        wd = day.weekday()
        d_assignments = assignments_by_day.get(day, {})
        d_attendance = attendance_by_day.get(day, {})
        for uid in all_staff_ids:
            on_leave = any(s <= day <= e for s, e in leaves_by_staff.get(uid, []))
            is_off = wd in weekoff_by_staff.get(uid, set())
            if on_leave or is_off:
                emp_totals[uid]['off_leave'] += 1
                continue
            assignment = d_assignments.get(uid)
            if not assignment:
                continue
            record = d_attendance.get(uid)
            if record and record.clock_in_time:
                emp_totals[uid]['late' if record.is_late else 'on_time'] += 1
            else:
                shift_start = timezone.make_aware(datetime.combine(day, assignment.shift.start_time))
                grace_end = shift_start + timedelta(minutes=assignment.shift.grace_minutes)
                if day < today or (day == today and now > grace_end):
                    emp_totals[uid]['absent'] += 1

    employee_stats = []
    for profile in all_active_profiles:
        if dept and profile.department != dept:
            continue
        totals = emp_totals[profile.user_id]
        scheduled = totals['on_time'] + totals['late'] + totals['absent']
        rate = round((totals['on_time'] + totals['late']) / scheduled * 100) if scheduled else None
        employee_stats.append({
            'name': profile.user.get_full_name() or profile.user.username,
            'department': profile.get_department_display() or 'Unassigned',
            'on_time': totals['on_time'], 'late': totals['late'],
            'absent': totals['absent'], 'off_leave': totals['off_leave'],
            'rate': rate,
        })
    employee_stats.sort(key=lambda e: (e['department'], e['name']))

    records = list(AttendanceRecord.objects.filter(date__gte=date_from, date__lte=today).select_related('staff'))
    flagged = [r for r in records if not r.clock_in_within_geofence or (r.clock_out_time and not r.clock_out_within_geofence)]

    return render(request, 'dashboard/hrm_attendance_report.html', {
        'roster_page': roster_page, 'roster_base_qs': roster_base_qs, 'roster_date': roster_date,
        'roster_prev': roster_date - timedelta(days=1), 'roster_next': roster_date + timedelta(days=1),
        'roster_today': today, 'roster_status_filter': roster_status_filter, 'roster_q': roster_q,
        'status_counts': status_counts, 'total_active': len(all_rows), 'dept': dept,
        'chart_payload_json': json.dumps(chart_payload), 'chart_height': chart_height,
        'employee_stats': employee_stats, 'department_options': StaffProfile.DEPARTMENT_CHOICES,
        'flagged': flagged[:20], 'days': days,
    })

@hrm_manager_required
def hrm_attendance_report_pdf(request):
  today = timezone.localdate()
  now = timezone.now()

  start_str = request.GET.get('start_date', '')
  end_str = request.GET.get('end_date', '')
  dept = request.GET.get('dept', '')

  try:
    start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
  except (ValueError, TypeError):
    messages.error(request, 'Please choose a valid date range.')
    return redirect('dashboard:hrm_attendance_report')

  if end_date < start_date:
    messages.error(request, "End date can't be before start date.")
    return redirect('dashboard:hrm_attendance_report')

  span_days = (end_date - start_date).days + 1
  if span_days > 30:
    messages.error(
        request, 'Reports are limited to 30 days maximum per export.'
    )
    return redirect('dashboard:hrm_attendance_report')

  profiles = list(
      StaffProfile.objects.filter(
          employment_status__in=['active', 'on_leave']
      ).select_related('user')
  )
  if dept:
    profiles = [p for p in profiles if p.department == dept]

  staff_ids = [p.user_id for p in profiles]
  profile_by_id = {p.user_id: p for p in profiles}
  report_days = [(start_date + timedelta(days=i)) for i in range(span_days)]

  weekoff_by_staff = {
      off.staff_id: set(off.weekdays)
      for off in StaffWeeklyOff.objects.filter(staff_id__in=staff_ids)
  }

  leaves_by_staff = {}
  for lr in LeaveRequest.objects.filter(
      staff_id__in=staff_ids,
      status='approved',
      end_date__gte=start_date,
      start_date__lte=end_date,
  ):
    leaves_by_staff.setdefault(lr.staff_id, []).append(
        (lr.start_date, lr.end_date)
    )

  assignments_by_day = {}
  for a in ShiftAssignment.objects.filter(
      staff_id__in=staff_ids, date__gte=start_date, date__lte=end_date
  ).select_related('shift'):
    assignments_by_day.setdefault(a.date, {})[a.staff_id] = a

  attendance_by_day = {}
  for rec in AttendanceRecord.objects.filter(
      staff_id__in=staff_ids, date__gte=start_date, date__lte=end_date
  ):
    attendance_by_day.setdefault(rec.date, {})[rec.staff_id] = rec

  emp_totals = {
      uid: {
          'on_time': 0,
          'late': 0,
          'absent': 0,
          'off_leave': 0,
          'pending': 0,
          'scheduled': 0,
          'no_checkout': 0,
          'unscheduled_present': 0,
      }
      for uid in staff_ids
  }

  daily_breakdown = []
  total_on_time = 0
  total_late = 0
  total_absent = 0
  total_off_leave = 0
  total_pending = 0
  total_no_checkout = 0
  total_unscheduled_present = 0

  for day in report_days:
    wd = day.weekday()
    d_assignments = assignments_by_day.get(day, {})
    d_attendance = attendance_by_day.get(day, {})
    day_rows = []

    for uid in staff_ids:
      profile = profile_by_id[uid]
      name = profile.user.get_full_name() or profile.user.username
      dept_name = profile.get_department_display() or 'Unassigned'

      on_leave = any(s <= day <= e for s, e in leaves_by_staff.get(uid, []))
      is_off = wd in weekoff_by_staff.get(uid, set())
      record = d_attendance.get(uid)
      assignment = d_assignments.get(uid)

      # 1. On Leave
      if on_leave:
        emp_totals[uid]['off_leave'] += 1
        total_off_leave += 1
        clock_in_str = (
            timezone.localtime(record.clock_in_time).strftime('%H:%M')
            if record and record.clock_in_time
            else '—'
        )
        clock_out_str = (
            timezone.localtime(record.clock_out_time).strftime('%H:%M')
            if record and record.clock_out_time
            else '—'
        )

        day_rows.append({
            'name': name,
            'department': dept_name,
            'status': 'On Leave',
            'status_code': 'on_leave',
            'shift': 'On Leave (Approved)',
            'clock_in': clock_in_str,
            'clock_out': clock_out_str,
        })
        continue

      # 2. Weekly Off Day
      if is_off:
        emp_totals[uid]['off_leave'] += 1
        total_off_leave += 1
        clock_in_str = (
            timezone.localtime(record.clock_in_time).strftime('%H:%M')
            if record and record.clock_in_time
            else '—'
        )
        clock_out_str = (
            timezone.localtime(record.clock_out_time).strftime('%H:%M')
            if record and record.clock_out_time
            else '—'
        )

        day_rows.append({
            'name': name,
            'department': dept_name,
            'status': 'Weekly Off',
            'status_code': 'off',
            'shift': 'Weekly Off Day',
            'clock_in': clock_in_str,
            'clock_out': clock_out_str,
        })
        continue

      # 3. No Shift Assigned
      if not assignment:
        if record and record.clock_in_time:
          # Staff clocked in even though no shift was scheduled
          emp_totals[uid]['unscheduled_present'] += 1
          total_unscheduled_present += 1

          clock_in_formatted = timezone.localtime(
              record.clock_in_time
          ).strftime('%H:%M')
          if record.clock_out_time:
            clock_out_formatted = timezone.localtime(
                record.clock_out_time
            ).strftime('%H:%M')
          else:
            clock_out_formatted = (
                'Never Signed Out' if day < today else 'In Progress'
            )

          day_rows.append({
              'name': name,
              'department': dept_name,
              'status': 'Unscheduled (Present)',
              'status_code': 'unscheduled',
              'shift': 'Unscheduled Shift',
              'clock_in': clock_in_formatted,
              'clock_out': clock_out_formatted,
          })
        else:
          day_rows.append({
              'name': name,
              'department': dept_name,
              'status': 'No Shift',
              'status_code': 'no_shift',
              'shift': 'No Shift Scheduled',
              'clock_in': '—',
              'clock_out': '—',
          })
        continue

      # 4. Assigned Shift Logic
      shift = assignment.shift

      shift_start_dt = timezone.make_aware(
          datetime.combine(day, shift.start_time)
      )
      if shift.end_time <= shift.start_time:
        shift_end_dt = timezone.make_aware(
            datetime.combine(day + timedelta(days=1), shift.end_time)
        )
      else:
        shift_end_dt = timezone.make_aware(
            datetime.combine(day, shift.end_time)
        )

      grace_end_dt = shift_start_dt + timedelta(minutes=shift.grace_minutes)

      if record and record.clock_in_time:
        emp_totals[uid]['scheduled'] += 1
        clock_in_formatted = timezone.localtime(
            record.clock_in_time
        ).strftime('%H:%M')

        if record.clock_out_time:
          clock_out_formatted = timezone.localtime(
              record.clock_out_time
          ).strftime('%H:%M')
        else:
          if now > (shift_end_dt + timedelta(minutes=shift.grace_minutes)):
            clock_out_formatted = 'Never Signed Out'
            emp_totals[uid]['no_checkout'] += 1
            total_no_checkout += 1
          else:
            clock_out_formatted = 'In Progress'

        if record.is_late:
          emp_totals[uid]['late'] += 1
          total_late += 1
          status_str = f'Late ({record.late_minutes}m)'
          status_code = 'late'
        else:
          emp_totals[uid]['on_time'] += 1
          total_on_time += 1
          status_str = 'On Time'
          status_code = 'on_time'

        day_rows.append({
            'name': name,
            'department': dept_name,
            'status': status_str,
            'status_code': status_code,
            'shift': f'{shift.name} ({shift.start_time.strftime("%H:%M")}-{shift.end_time.strftime("%H:%M")})',
            'clock_in': clock_in_formatted,
            'clock_out': clock_out_formatted,
        })
      else:
        # Assigned Shift but No Clock-in
        if now < shift_start_dt:
          emp_totals[uid]['pending'] += 1
          total_pending += 1
          status_str = 'Scheduled (Pending)'
          status_code = 'pending'
        elif shift_start_dt <= now <= shift_end_dt:
          if now <= grace_end_dt:
            emp_totals[uid]['pending'] += 1
            total_pending += 1
            status_str = 'In Grace Period'
            status_code = 'pending'
          else:
            emp_totals[uid]['scheduled'] += 1
            emp_totals[uid]['absent'] += 1
            total_absent += 1
            status_str = 'Absent (Overdue)'
            status_code = 'absent'
        else:
          emp_totals[uid]['scheduled'] += 1
          emp_totals[uid]['absent'] += 1
          total_absent += 1
          status_str = 'Absent'
          status_code = 'absent'

        day_rows.append({
            'name': name,
            'department': dept_name,
            'status': status_str,
            'status_code': status_code,
            'shift': f'{shift.name} ({shift.start_time.strftime("%H:%M")}-{shift.end_time.strftime("%H:%M")})',
            'clock_in': '—',
            'clock_out': '—',
        })

    day_rows.sort(key=lambda r: r['name'])
    daily_breakdown.append(
        {'date': day, 'is_today': day == today, 'rows': day_rows}
    )

  rows = []
  for profile in profiles:
    totals = emp_totals[profile.user_id]
    scheduled = totals['scheduled']
    attended = totals['on_time'] + totals['late']
    rate = round((attended / scheduled) * 100) if scheduled else None

    rows.append({
        'name': profile.user.get_full_name() or profile.user.username,
        'department': profile.get_department_display() or 'Unassigned',
        'on_time': totals['on_time'],
        'late': totals['late'],
        'absent': totals['absent'],
        'off_leave': totals['off_leave'],
        'pending': totals['pending'],
        'no_checkout': totals['no_checkout'],
        'unscheduled_present': totals['unscheduled_present'],
        'scheduled': scheduled,
        'rate': rate,
    })

  rows.sort(key=lambda r: (r['department'], r['name']))

  total_evaluated = total_on_time + total_late + total_absent
  total_attended = total_on_time + total_late
  overall_attendance_rate = (
      round((total_attended / total_evaluated) * 100, 1)
      if total_evaluated
      else 0.0
  )

  dept_label = (
      dict(StaffProfile.DEPARTMENT_CHOICES).get(dept, 'All Departments')
      if dept
      else 'All Departments'
  )

  html_string = render_to_string(
      'dashboard/hrm_attendance_report_pdf.html',
      {
          'rows': rows,
          'daily_breakdown': daily_breakdown,
          'start_date': start_date,
          'end_date': end_date,
          'span_days': span_days,
          'dept_label': dept_label,
          'generated_at': timezone.now(),
          'generated_by': request.user,
          'total_staff_count': len(profiles),
          'total_on_time': total_on_time,
          'total_late': total_late,
          'total_absent': total_absent,
          'total_off_leave': total_off_leave,
          'total_pending': total_pending,
          'total_no_checkout': total_no_checkout,
          'total_unscheduled_present': total_unscheduled_present,
          'total_evaluated': total_evaluated,
          'overall_attendance_rate': overall_attendance_rate,
      },
  )

  pdf_file = HTML(
      string=html_string, base_url=request.build_absolute_uri('/')
  ).write_pdf()
  response = HttpResponse(pdf_file, content_type='application/pdf')
  response['Content-Disposition'] = (
      f'attachment;'
      f' filename="attendance_report_{start_date}_{end_date}.pdf"'
  )
  return response

@hrm_manager_required
def hrm_shift_calendar(request):
    if request.method == 'POST':
        form = BulkShiftAssignForm(request.POST)
        if form.is_valid():
            created, skipped = ShiftAssignment.bulk_assign(
                staff=form.cleaned_data['staff'], shift=form.cleaned_data['shift'],
                start_date=form.cleaned_data['start_date'], end_date=form.cleaned_data['end_date'],
                assigned_by=request.user,
            )
            if created:
                messages.success(request, f"Assigned {len(created)} day(s).")
            if skipped:
                detail = ", ".join(f"{d.strftime('%b %d')} ({reason})" for d, reason in skipped[:8])
                more = f" and {len(skipped) - 8} more" if len(skipped) > 8 else ""
                messages.warning(request, f"Skipped {len(skipped)} day(s): {detail}{more}.")
            return redirect('dashboard:hrm_shift_calendar')
    else:
        form = BulkShiftAssignForm()

    today = timezone.localdate()

    # ---------- Upcoming Assignments: filters + pagination ----------
    assignments_qs = ShiftAssignment.objects.filter(date__gte=today).select_related(
        'staff', 'staff__staff_profile', 'shift'
    ).order_by('date')

    f_staff = request.GET.get('staff', '')
    f_shift = request.GET.get('shift', '')
    f_status = request.GET.get('status', '')
    f_date_from = request.GET.get('date_from', '')
    f_date_to = request.GET.get('date_to', '')

    if f_staff:
        assignments_qs = assignments_qs.filter(staff_id=f_staff)
    if f_shift:
        assignments_qs = assignments_qs.filter(shift_id=f_shift)
    if f_status:
        assignments_qs = assignments_qs.filter(status=f_status)
    if f_date_from:
        assignments_qs = assignments_qs.filter(date__gte=f_date_from)
    if f_date_to:
        assignments_qs = assignments_qs.filter(date__lte=f_date_to)

    upcoming_page = Paginator(assignments_qs, 10).get_page(request.GET.get('page', 1))
    upcoming_base_qs = _qs_without(request, 'page')
    total_upcoming = ShiftAssignment.objects.filter(date__gte=today).count()

    # ---------- Shift change requests: filters + pagination ----------
    swap_status = request.GET.get('swap_status', 'pending')
    swap_mode = request.GET.get('swap_mode', '')
    swap_qs = ShiftSwapRequest.objects.select_related(
        'requested_by', 'requested_by__staff_profile', 'requested_shift', 'trade_with'
    ).order_by('-created_at')
    if swap_status:
        swap_qs = swap_qs.filter(status=swap_status)
    if swap_mode:
        swap_qs = swap_qs.filter(mode=swap_mode)

    swap_page = Paginator(swap_qs, 6).get_page(request.GET.get('swap_page', 1))
    swap_base_qs = _qs_without(request, 'swap_page')
    swap_status_base_qs = _qs_without(request, 'swap_status', 'swap_page')
    swap_mode_base_qs = _qs_without(request, 'swap_mode', 'swap_page')
    pending_swap_count = ShiftSwapRequest.objects.filter(status='pending').count()

    # ---------- Calendar month grid ----------
    current_month_param = request.GET.get('month', '')
    if current_month_param:
        cal_year, cal_month = map(int, current_month_param.split('-'))
    else:
        cal_year, cal_month = today.year, today.month

    first_of_month = date_cls(cal_year, cal_month, 1)
    grid_start = first_of_month - timedelta(days=first_of_month.weekday())
    grid_end = grid_start + timedelta(days=41)
    month_assignments = ShiftAssignment.objects.filter(
        date__gte=grid_start, date__lte=grid_end
    ).select_related('staff', 'shift')

    by_date = {}
    for a in month_assignments:
        by_date.setdefault(a.date, []).append(a)

    weeks, day = [], grid_start
    for _ in range(6):
        week = []
        for _ in range(7):
            week.append({
                'date': day, 'in_month': day.month == cal_month, 'is_today': day == today,
                'assignments': by_date.get(day, []),
            })
            day += timedelta(days=1)
        weeks.append(week)

    prev_month = (cal_year - 1, 12) if cal_month == 1 else (cal_year, cal_month - 1)
    next_month = (cal_year + 1, 1) if cal_month == 12 else (cal_year, cal_month + 1)
    month_base_qs = _qs_without(request, 'month')
    calendar_day_link_qs = _qs_without(request, 'date_from', 'date_to', 'page')

    return render(request, 'dashboard/hrm_shift_calendar.html', {
        'form': form,
        'upcoming_page': upcoming_page, 'upcoming_base_qs': upcoming_base_qs, 'total_upcoming': total_upcoming,
        'staff_options': User.objects.filter(is_staff=True).order_by('first_name'),
        'shift_options': Shift.objects.all(),
        'status_options': ShiftAssignment.STATUS_CHOICES,
        'f_staff': f_staff, 'f_shift': f_shift, 'f_status': f_status,
        'f_date_from': f_date_from, 'f_date_to': f_date_to,
        'swap_page': swap_page, 'swap_base_qs': swap_base_qs,
        'swap_status_base_qs': swap_status_base_qs, 'swap_mode_base_qs': swap_mode_base_qs,
        'swap_status': swap_status, 'swap_mode': swap_mode, 'pending_swap_count': pending_swap_count,
        'weeks': weeks, 'cal_month_label': first_of_month.strftime('%B %Y'),
        'prev_month': f"{prev_month[0]}-{prev_month[1]:02d}",
        'next_month': f"{next_month[0]}-{next_month[1]:02d}",
        'this_month_str': f"{today.year}-{today.month:02d}",
        'current_month_param': current_month_param, 'month_base_qs': month_base_qs,
        'calendar_day_link_qs': calendar_day_link_qs,
    })

@hrm_manager_required
def hrm_weekly_off(request):
    if request.method == 'POST':
        form = StaffWeeklyOffForm(request.POST)
        if form.is_valid():
            staff = form.cleaned_data['staff']
            weekdays = sorted(int(w) for w in form.cleaned_data['weekdays'])
            if weekdays:
                StaffWeeklyOff.objects.update_or_create(staff=staff, defaults={'weekdays': weekdays})
            else:
                StaffWeeklyOff.objects.filter(staff=staff).delete()
            messages.success(request, f"Updated weekly off days for {staff.get_full_name() or staff.username}.")
            return redirect('dashboard:hrm_weekly_off')
    else:
        form = StaffWeeklyOffForm()

    staff_offs = {
        off.staff: off.weekday_labels()
        for off in StaffWeeklyOff.objects.select_related('staff').order_by('staff__first_name')
        if off.weekdays
    }
    return render(request, 'dashboard/hrm_weekly_off.html', {'form': form, 'staff_offs': staff_offs})

@hrm_manager_required
def hrm_weekly_off_lookup(request, user_id):
    off = StaffWeeklyOff.objects.filter(staff_id=user_id).first()
    return JsonResponse({'weekdays': off.weekdays if off else []})

@hrm_manager_required
def hrm_shift_swap_review(request, swap_id, action):
    if request.method != 'POST':
        return redirect('dashboard:hrm_shift_calendar')
    swap = get_object_or_404(ShiftSwapRequest, id=swap_id, status='pending')
    if action == 'approve':
        swap.apply(reviewed_by=request.user)
        messages.success(request, "Swap approved and shifts updated.")
    else:
        reason = request.POST.get('rejection_reason', '').strip()
        if not reason:
            messages.error(request, "Please provide a reason for rejecting this request.")
            return redirect('dashboard:hrm_shift_calendar')
        swap.status = 'rejected'
        swap.rejection_reason = reason
        swap.reviewed_by = request.user
        swap.reviewed_at = timezone.now()
        swap.save()
        messages.success(request, "Swap rejected.")
    return redirect('dashboard:hrm_shift_calendar')

@hrm_manager_required
def hrm_leave_requests(request):
    status_filter = request.GET.get('status', 'pending')
    today = timezone.localdate()
    requests_qs = LeaveRequest.objects.select_related('staff', 'leave_type').order_by('-created_at')
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)
    requests_qs = list(requests_qs)

    balances_by_staff = {}
    for b in LeaveBalance.objects.filter(year=today.year).select_related('leave_type'):
        balances_by_staff.setdefault(b.staff_id, []).append(b)
    for r in requests_qs:
        r.staff_balances = balances_by_staff.get(r.staff_id, [])

    return render(request, 'dashboard/hrm_leave_requests.html', {'requests': requests_qs, 'status_filter': status_filter})

@hrm_manager_required
def hrm_leave_review(request, leave_id, action):
    if request.method != 'POST':
        return redirect('dashboard:hrm_leave_requests')
    leave = get_object_or_404(LeaveRequest, id=leave_id, status='pending')

    if action == 'approve':
        balance, _ = LeaveBalance.objects.get_or_create(
            staff=leave.staff, leave_type=leave.leave_type, year=leave.start_date.year,
            defaults={'allocated_days': leave.leave_type.default_days_per_year},
        )
        if leave.days_count > balance.remaining_days:
            messages.error(request, f"{leave.staff} only has {balance.remaining_days} day(s) of {leave.leave_type} left, this request needs {leave.days_count}.")
            return redirect('dashboard:hrm_leave_requests')
        balance.used_days += leave.days_count
        balance.save(update_fields=['used_days'])
        leave.status = 'approved'
    else:
        reason = request.POST.get('rejection_reason', '').strip()
        if not reason:
            messages.error(request, "Please provide a reason for rejecting this request.")
            return redirect('dashboard:hrm_leave_requests')
        leave.status = 'rejected'
        leave.rejection_reason = reason
    leave.reviewed_by = request.user
    leave.reviewed_at = timezone.now()
    leave.save()
    messages.success(request, f"Leave request {leave.status}.")
    return redirect('dashboard:hrm_leave_requests')

@hrm_manager_required
def hrm_payroll_list(request):
    period_month = int(request.GET.get('month', timezone.localdate().month))
    period_year = int(request.GET.get('year', timezone.localdate().year))

    # Updated related lookup: 'staff__staff_profile'
    records_qs = PayrollRecord.objects.filter(
        period_month=period_month, 
        period_year=period_year
    ).select_related('staff', 'staff__staff_profile').order_by('-generated_at')

    # Exclude already generated staff
    already_processed_ids = records_qs.values_list('staff_id', flat=True)
    pending_qs = StaffProfile.objects.exclude(
        user_id__in=already_processed_ids
    ).select_related('user').order_by('user__first_name')

    # Paginate both sets
    records_page = Paginator(records_qs, 10).get_page(request.GET.get('records_page', 1))
    pending_page = Paginator(pending_qs, 10).get_page(request.GET.get('pending_page', 1))

    return render(request, 'dashboard/hrm_payroll_list.html', {
        'records_page': records_page,
        'pending_page': pending_page,
        'period_month': period_month,
        'period_year': period_year,
    })

@hrm_manager_required
def hrm_leave_requests(request):
  status_filter = request.GET.get("status", "pending")
  search_query = request.GET.get("q", "").strip()
  start_date = request.GET.get("start_date", "")
  end_date = request.GET.get("end_date", "")
  selected_month = request.GET.get("month", "")
  selected_week = request.GET.get("week", "")
  page_number = request.GET.get("page", 1)

  today = timezone.localdate()

  # Base Queryset
  base_qs = LeaveRequest.objects.select_related(
      "staff", "leave_type", "staff__staff_profile"
  )

  # Counters computed before filtering current list tab
  metrics = {
      "total": base_qs.count(),
      "pending": base_qs.filter(status="pending").count(),
      "approved": base_qs.filter(status="approved").count(),
      "rejected": base_qs.filter(status="rejected").count(),
  }

  requests_qs = base_qs.order_by("-created_at")

  # Apply Status Filter
  if status_filter and status_filter != "all":
    requests_qs = requests_qs.filter(status=status_filter)

  # Apply Search Filter
  if search_query:
    requests_qs = requests_qs.filter(
        Q(staff__first_name__icontains=search_query)
        | Q(staff__last_name__icontains=search_query)
        | Q(staff__username__icontains=search_query)
        | Q(reason__icontains=search_query)
    )

  # Apply Date Range
  if start_date:
    try:
      parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
      requests_qs = requests_qs.filter(start_date__gte=parsed_start)
    except ValueError:
      pass

  if end_date:
    try:
      parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
      requests_qs = requests_qs.filter(end_date__lte=parsed_end)
    except ValueError:
      pass

  # Apply Month Filter
  if selected_month:
    try:
      m_year, m_month = map(int, selected_month.split("-"))
      requests_qs = requests_qs.filter(
          start_date__year=m_year, start_date__month=m_month
      )
    except ValueError:
      pass

  # Apply Week Filter
  if selected_week:
    try:
      w_year, w_week = map(int, selected_week.split("-W"))
      requests_qs = requests_qs.filter(
          start_date__year=w_year, start_date__week=w_week
      )
    except ValueError:
      pass

  # Pagination (10 requests per page)
  paginator = Paginator(requests_qs, 10)
  try:
    requests_page = paginator.page(page_number)
  except PageNotAnInteger:
    requests_page = paginator.page(1)
  except EmptyPage:
    requests_page = paginator.page(paginator.num_pages)

  # Preload Leave Balances for page items
  staff_ids = [r.staff_id for r in requests_page]
  balances_by_staff = {}
  if staff_ids:
    for b in LeaveBalance.objects.filter(
        year=today.year, staff_id__in=staff_ids
    ).select_related("leave_type"):
      balances_by_staff.setdefault(b.staff_id, []).append(b)

  for r in requests_page:
    r.staff_balances = balances_by_staff.get(r.staff_id, [])

  # Preserve search & filter parameters for pagination links
  query_params = request.GET.copy()
  if "page" in query_params:
    del query_params["page"]
  extra_params = query_params.urlencode()

  context = {
      "requests": requests_page,
      "status_filter": status_filter,
      "search_query": search_query,
      "start_date": start_date,
      "end_date": end_date,
      "selected_month": selected_month,
      "selected_week": selected_week,
      "metrics": metrics,
      "extra_params": extra_params,
  }

  return render(request, "dashboard/hrm_leave_requests.html", context)

@hrm_manager_required
def hrm_payroll_create(request, user_id):
    staff_user = get_object_or_404(User, id=user_id)
    profile = getattr(staff_user, 'staff_profile', None)
    
    if request.method == 'POST':
        form = PayrollForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.staff = staff_user
            record.generated_by = request.user
            record.status = 'draft'  # Explicitly maintain draft status
            record.save()
            messages.success(request, f"Payroll draft created for {staff_user.get_full_name() or staff_user.username}.")
            return redirect('dashboard:hrm_payroll_list')
    else:
        today = timezone.localdate()
        form = PayrollForm(initial={
            'period_month': today.month, 
            'period_year': today.year, 
            'basic_salary': profile.pay_rate if profile else 0
        })
        
    return render(request, 'dashboard/hrm_payroll_create.html', {
        'form': form, 
        'staff_user': staff_user,
        'is_edit': False,
    })

@hrm_manager_required
def hrm_payroll_edit(request, record_id):
    # Only allow editing if status is still 'draft'
    record = get_object_or_404(PayrollRecord, id=record_id, status='draft')
    staff_user = record.staff

    if request.method == 'POST':
        # Bind request.POST to existing record instance
        form = PayrollForm(request.POST, instance=record)
        if form.is_valid():
            updated_record = form.save(commit=False)
            updated_record.status = 'draft'  # Ensure status never shifts to finalized on edit
            updated_record.save()
            messages.success(request, f"Payroll draft updated for {staff_user.get_full_name() or staff_user.username}.")
            return redirect('dashboard:hrm_payroll_list')
    else:
        form = PayrollForm(instance=record)

    return render(request, 'dashboard/hrm_payroll_create.html', {
        'form': form,
        'staff_user': staff_user,
        'record': record,
        'is_edit': True,
    })

@hrm_manager_required
def hrm_payroll_finalize(request, record_id):
    if request.method != 'POST':
        return redirect('dashboard:hrm_payroll_list')
    
    record = get_object_or_404(PayrollRecord, id=record_id, status='draft')
    record.status = 'finalized'
    record.save(update_fields=['status'])
    messages.success(request, f"Payroll finalized for {record.staff.get_full_name() or record.staff.username}.")
    return redirect('dashboard:hrm_payroll_list')

@hrm_manager_required
def hrm_performance_list(request):
    reviews = PerformanceReview.objects.select_related('staff', 'reviewer').order_by('-created_at')
    return render(request, 'dashboard/hrm_performance_list.html', {'reviews': reviews})

@hrm_manager_required
def hrm_performance_create(request, user_id):
    staff_user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = PerformanceReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.staff = staff_user
            review.reviewer = request.user
            review.save()
            messages.success(request, "Review saved as draft.")
            return redirect('dashboard:hrm_staff_detail', user_id=staff_user.id)
    else:
        form = PerformanceReviewForm()
    return render(request, 'dashboard/hrm_performance_create.html', {'form': form, 'staff_user': staff_user})

@hrm_manager_required
def hrm_performance_finalize(request, review_id):
    if request.method != 'POST':
        return redirect('dashboard:hrm_performance_list')
    review = get_object_or_404(PerformanceReview, id=review_id, status='draft')
    review.status = 'finalized'
    review.save(update_fields=['status'])
    messages.success(request, "Review finalized and shared with the staff member.")
    return redirect('dashboard:hrm_performance_list')

@hrm_manager_required
def hrm_shift_assignment_edit(request, assignment_id):
    assignment = get_object_or_404(ShiftAssignment, id=assignment_id)
    if request.method == 'POST':
        form = ShiftAssignmentForm(request.POST, instance=assignment)
        if form.is_valid():
            try:
                form.instance.full_clean()
                form.save()
                messages.success(request, "Shift updated.")
                return redirect('dashboard:hrm_shift_calendar')
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
    else:
        form = ShiftAssignmentForm(instance=assignment)
    return render(request, 'dashboard/hrm_shift_assignment_edit.html', {'form': form, 'assignment': assignment})

@hrm_manager_required
def hrm_shift_assignment_delete(request, assignment_id):
    if request.method != 'POST':
        return redirect('dashboard:hrm_shift_calendar')
    get_object_or_404(ShiftAssignment, id=assignment_id).delete()
    messages.success(request, "Shift assignment removed.")
    return redirect('dashboard:hrm_shift_calendar')

@login_required
def hrm_swap_conflict_check(request):
    date_str = request.GET.get('date')
    trade_with_id = request.GET.get('trade_with')
    staff_id = request.GET.get('staff_id') or request.user.id
    try:
        day = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return JsonResponse({'same': False})
    mine = ShiftAssignment.objects.filter(staff_id=staff_id, date=day).select_related('shift').first()
    theirs = ShiftAssignment.objects.filter(staff_id=trade_with_id, date=day).select_related('shift').first()
    same = bool(mine and theirs and mine.shift_id == theirs.shift_id)
    return JsonResponse({
        'same': same,
        'mine': mine.shift.name if mine else None,
        'theirs': theirs.shift.name if theirs else None,
    })