import uuid
import json
import base64
import io
import qrcode
from .permissions import can_access
from django.conf import settings
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
from store.forms import *
from django.db import transaction
from store.services import get_purchases_in_range, summarize_by_department
from .services import *

from decimal import Decimal
from django.urls import reverse
from datetime import datetime
from .permissions import can_manage_bookings_from_rooms

from datetime import datetime
from decimal import Decimal
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from weasyprint import HTML

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

    return render(request, 'dashboard/rooms.html', {
        'rooms': rooms,
        'counts': counts,
        'status_filter': status_filter,
        'open_maintenance_count': open_maintenance_count,
        'unclaimed_lost_count': unclaimed_lost_count,
        'active_bookings': active_bookings,
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

    MaintenanceRequest.objects.create(
        room=room,
        issue=issue,
        description=request.POST.get('description', '').strip(),
        priority=request.POST.get('priority', 'medium'),
        reported_by=request.user,
    )
    messages.success(request, f"Issue reported for Room {room.number}. Room marked under Maintenance.")
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
    req.save(update_fields=['status', 'resolved_by', 'resolved_at'])

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


# ==============================================================================
# DAILY USAGE LOGS (Generic Helpers & Views for Kitchen & Housekeeping)
# ==============================================================================

def _can_touch_usage_log(user, department):
    module = 'restaurant_kitchen' if department == 'kitchen' else 'rooms'
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
    mode = request.POST.get('mode')
    quantity = Decimal(request.POST.get('quantity') or '0')

    if quantity <= 0:
        messages.error(request, "Enter a quantity greater than zero.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))

    with transaction.atomic():
        if mode == 'stock':
            stock = get_object_or_404(StockItem, id=request.POST.get('stock_item'), department=department)
            if quantity > stock.quantity_on_hand:
                messages.error(request, f"Can't log more than what's in stock ({stock.quantity_on_hand} {stock.unit}).")
                return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))
            
            DailyUsageItem.objects.create(
                log=log,
                stock_item=stock,
                quantity=quantity,
                added_by=request.user
            )
            StockItem.objects.filter(id=stock.id).select_for_update().update(
                quantity_on_hand=stock.quantity_on_hand - quantity
            )
        else:
            name = request.POST.get('custom_name', '').strip()
            unit = request.POST.get('custom_unit', '')
            if not name:
                messages.error(request, "Enter a name for the item.")
                return redirect(request.META.get('HTTP_REFERER', 'dashboard:home'))
            
            DailyUsageItem.objects.create(
                log=log,
                custom_name=name,
                custom_unit=unit,
                quantity=quantity,
                added_by=request.user
            )

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
    return _usage_log_view(
        request, 'kitchen', 'dashboard/kitchen_usage_log.html', 'dashboard:restaurant_kitchen_display'
    )


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
# HOUSEKEEPING USAGE LOG ENDPOINTS
# ------------------------------------------------------------------------------

@login_required
def housekeeping_usage_log_view(request):
    return _usage_log_view(
        request, 'housekeeping', 'dashboard/housekeeping_usage_log.html', 'dashboard:rooms'
    )


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
                    name=data['new_item_name'], department=data['department'],
                    unit=data['new_item_unit'], reorder_level=data.get('reorder_level') or 0,
                )
            purchase = PurchaseLog.objects.create(
                department=data['department'], stock_item=stock_item, quantity=data['quantity'],
                unit_cost=data['unit_cost'], vat_inclusive=data.get('vat_inclusive', True),
                notes=data.get('notes', ''), purchased_by=request.user, purchased_at=data['purchased_at'],
            )
            for f in request.FILES.getlist('receipts'):
                PurchaseReceipt.objects.create(purchase=purchase, image=f)
            messages.success(request, f"Purchase logged: {stock_item.name} — KSh {purchase.total_cost:,.0f}")
            return redirect('dashboard:store')
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = PurchaseLogForm(initial={'purchased_at': today, 'vat_inclusive': True, 'department': 'kitchen'})

    today_purchases = PurchaseLog.objects.filter(purchased_at__date=today)
    today_total = sum((p.total_cost for p in today_purchases), Decimal('0'))
    week_purchases = PurchaseLog.objects.filter(purchased_at__date__gte=week_ago)
    week_total = sum((p.total_cost for p in week_purchases), Decimal('0'))

    recent_purchases = PurchaseLog.objects.select_related('stock_item', 'purchased_by').prefetch_related('receipts')[:15]
    low_stock_items = [i for i in StockItem.objects.filter(is_active=True) if i.stock_status != 'ok']

    chart_days = [(today - timezone.timedelta(days=i)) for i in range(13, -1, -1)]
    dept_codes = [c for c, _ in StockItem.DEPARTMENT_CHOICES]
    dept_labels = dict(StockItem.DEPARTMENT_CHOICES)
    daily_by_dept = {code: [] for code in dept_codes}
    for day in chart_days:
        day_purchases = PurchaseLog.objects.filter(purchased_at__date=day)
        for code in dept_codes:
            total = sum((p.total_cost for p in day_purchases if p.department == code), Decimal('0'))
            daily_by_dept[code].append(float(total))

    return render(request, 'dashboard/store.html', {
        'form': form, 'today_total': today_total, 'week_total': week_total,
        'recent_purchases': recent_purchases, 'low_stock_items': low_stock_items,
        'chart_labels': json.dumps([d.strftime('%b %d') for d in chart_days]),
        'chart_datasets': json.dumps([{'label': dept_labels[c], 'data': daily_by_dept[c]} for c in dept_codes]),
        'department_choices': StockItem.DEPARTMENT_CHOICES,
    })


@staff_module_required('store')
def store_usage_logs_view(request):
    dept = request.GET.get('department', 'kitchen')
    preset = request.GET.get('preset', 'week')
    today = timezone.localdate()
    days = {'today': 0, 'week': 7, 'month': 30, 'quarter': 90, 'year': 365}.get(preset, 7)
    date_from = today - timezone.timedelta(days=days)

    usage_items = DailyUsageItem.objects.filter(
        log__department=dept, log__date__gte=date_from, log__date__lte=today
    ).select_related('stock_item', 'added_by', 'log').order_by('-log__date', '-added_at')

    wastage_items = WastageLog.objects.filter(
        stock_item__department='bar', logged_at__date__gte=date_from, logged_at__date__lte=today
    ).select_related('stock_item', 'logged_by').order_by('-logged_at')

    # Value-based trend, not raw quantity, this is what makes units comparable.
    chart_days = [(date_from + timezone.timedelta(days=i)) for i in range((today - date_from).days + 1)]
    daily_values = []
    for day in chart_days:
        day_value = sum(
            (float(i.quantity) * float(i.stock_item.last_unit_cost) if i.stock_item else 0)
            for i in usage_items if i.log.date == day
        )
        daily_values.append(round(day_value, 2))

    today_log, _ = DailyUsageLog.objects.get_or_create(department=dept, date=today)
    today_items = today_log.items.select_related('stock_item', 'added_by').order_by('-added_at')
    stock_items = StockItem.objects.filter(department=dept, is_active=True)

    return render(request, 'dashboard/store_usage_logs.html', {
        'usage_items': usage_items, 'wastage_items': wastage_items,
        'department': dept, 'preset': preset, 'date_from': date_from, 'date_to': today,
        'chart_labels': json.dumps([d.strftime('%b %d') for d in chart_days]),
        'chart_data': json.dumps(daily_values),
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

    return render(request, 'dashboard/store_expenditure.html', {
        'summary': summary, 'date_from': date_from, 'date_to': date_to, 'preset': preset,
        'grand_incl': grand_incl, 'grand_excl': grand_excl, 'grand_vat': grand_vat,
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
    
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="expenditure_{date_from}_to_{date_to}.pdf"'
    return response

import json
from django.shortcuts import render

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
        'items': items, 'department_filter': department_filter,
        'department_choices': StockItem.DEPARTMENT_CHOICES,
        'chart_labels': json.dumps([i.name for i in items]),
        'chart_levels': json.dumps([float(i.quantity_on_hand) for i in items]),
        'chart_reorder': json.dumps([float(i.reorder_level) for i in items]),
        'chart_colors': json.dumps(['#dc2626' if i.stock_status == 'danger' else '#C9A227' if i.stock_status == 'warning' else '#0B6B3A' for i in items]),
    })

### end of store views

#### start of receipt views

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