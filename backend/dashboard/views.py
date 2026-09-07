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

from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML

from public_site.models import Booking, Order, RoomType, ConferenceRoom
from public_site.services import get_available_rooms

@login_required
def dashboard_home(request):
    context = {
        'modules': MODULES,
        'allowed_modules': get_allowed_modules(request.user),
    }
    if 'gate' in get_allowed_modules(request.user):
        context['gate_inside_count'] = GateLog.objects.filter(status='inside').count()
    return render(request, 'dashboard/home.html', context)


@staff_module_required('reception')
def reception_view(request):
    today = timezone.localdate()

    booking_status = request.GET.get('booking_status', '')
    booking_query = request.GET.get('booking_q', '').strip()
    bookings_qs = Booking.objects.select_related('room_type', 'conference_room').order_by('-created_at')
    if booking_status:
        bookings_qs = bookings_qs.filter(status=booking_status)
    if booking_query:
        bookings_qs = bookings_qs.filter(Q(guest_name__icontains=booking_query) | Q(guest_phone__icontains=booking_query))
    booking_page_obj = Paginator(bookings_qs, 15).get_page(request.GET.get('booking_page'))

    order_status = request.GET.get('order_status', '')
    orders_qs = Order.objects.prefetch_related('items__menu_item').order_by('-created_at')
    if order_status:
        orders_qs = orders_qs.filter(status=order_status)
    order_page_obj = Paginator(orders_qs, 15).get_page(request.GET.get('order_page'))

    arrivals_today = Booking.objects.filter(check_in=today, status__in=['pending', 'confirmed']).count()
    departures_today = Booking.objects.filter(check_out=today, status='checked_in').count()
    pending_orders_count = Order.objects.filter(status='pending').count()
    today_revenue = Payment.objects.filter(created_at__date=today).aggregate(total=Sum('amount'))['total'] or 0

    return render(request, 'dashboard/reception.html', {
        'booking_page_obj': booking_page_obj,
        'order_page_obj': order_page_obj,
        'booking_status': booking_status,
        'booking_query': booking_query,
        'order_status': order_status,
        'arrivals_today': arrivals_today,
        'departures_today': departures_today,
        'pending_orders_count': pending_orders_count,
        'today_revenue': today_revenue,
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

    booking.status = new_status
    booking.save(update_fields=['status'])
    messages.success(request, f"Booking for {booking.guest_name} marked as {booking.get_status_display()}.")
    return redirect('dashboard:reception')


@staff_module_required('reception')
def reception_order_action(request, order_id, action):
    if request.method != 'POST':
        return redirect('dashboard:reception')
    order = get_object_or_404(Order, id=order_id)

    if action == 'confirm' and order.status == 'pending':
        order.status = 'confirmed'
    elif action == 'cancel' and order.status in ('pending', 'confirmed'):
        order.status = 'cancelled'
    else:
        messages.error(request, "That action isn't available for this order's current status.")
        return redirect('dashboard:reception')

    order.save(update_fields=['status'])
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
def reception_record_payment(request, target_type, target_id):
    if target_type not in ('booking', 'order'):
        messages.error(request, "Invalid payment target.")
        return redirect('dashboard:reception')

    target = get_object_or_404(Booking if target_type == 'booking' else Order, id=target_id)

    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            setattr(payment, target_type, target)
            payment.received_by = request.user
            payment.save()
            messages.success(request, f"Payment of KSh {payment.amount:,.0f} recorded.")
            return redirect('dashboard:reception')
    else:
        form = PaymentForm()

    return render(request, 'dashboard/reception_payment.html', {
        'form': form, 'target': target, 'target_type': target_type,
    })


@staff_module_required('rooms')
def rooms_view(request):
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Rooms'})


@staff_module_required('restaurant_kitchen')
def restaurant_kitchen_view(request):
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Restaurant & Kitchen'})


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
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Store & Inventory'})