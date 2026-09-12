import json
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import RoomType, MenuItem, Booking, Order, OrderItem
from .services import get_available_rooms

import random
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import Conversation, ChatMessage, EmailVerification


def home(request):
    room_types = RoomType.objects.filter(is_active=True).prefetch_related('images')
    all_food = MenuItem.objects.filter(item_type='food', is_available=True).select_related('stock_item').prefetch_related('images')
    all_drinks = MenuItem.objects.filter(item_type='drink', is_available=True).select_related('stock_item').prefetch_related('images')
    food_items = [item for item in all_food if item.is_orderable]
    drink_items = [item for item in all_drinks if item.is_orderable]
    context = {
        'room_types': room_types,
        'food_items': food_items,
        'drink_items': drink_items,
    }
    return render(request, 'public_site/home.html', context)


def about(request):
    return render(request, 'public_site/about.html')


def check_availability(request):
    check_in_str = request.GET.get('check_in')
    check_out_str = request.GET.get('check_out')

    if not check_in_str or not check_out_str:
        return JsonResponse({'error': 'Both check_in and check_out are required.'}, status=400)

    try:
        check_in = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        check_out = datetime.strptime(check_out_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Dates must be in YYYY-MM-DD format.'}, status=400)

    if check_out <= check_in:
        return JsonResponse({'error': 'Check-out must be after check-in.'}, status=400)

    results = []
    for rt in RoomType.objects.filter(is_active=True):
        available = get_available_rooms(rt, check_in, check_out)
        results.append({
            'id': rt.id,
            'name': rt.name,
            'price_min': str(rt.price_min),
            'price_max': str(rt.price_max),
            'capacity': rt.capacity,
            'available': available,
        })

    return JsonResponse({'room_types': results})


@require_POST
def submit_booking(request):
    room_type_id = request.POST.get('room_type_id')
    check_in_str = request.POST.get('check_in')
    check_out_str = request.POST.get('check_out')
    guest_name = request.POST.get('guest_name', '').strip()
    guest_phone = request.POST.get('guest_phone', '').strip()
    guest_id_no = request.POST.get('guest_id_no', '').strip()

    if not all([room_type_id, check_in_str, check_out_str, guest_name, guest_phone]):
        return JsonResponse({'error': 'Missing required fields.'}, status=400)

    try:
        room_type = RoomType.objects.get(id=room_type_id, is_active=True)
        check_in = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        check_out = datetime.strptime(check_out_str, '%Y-%m-%d').date()
    except (RoomType.DoesNotExist, ValueError):
        return JsonResponse({'error': 'Invalid room type or dates.'}, status=400)

    available = get_available_rooms(room_type, check_in, check_out)
    if available < 1:
        return JsonResponse({'error': 'Sorry, that room type is no longer available for those dates.'}, status=409)

    # amount is the per-night rate, not the total stay cost, kept within
    # the room type's price_min/price_max range. Online bookings default
    # to the advertised starting rate; front desk can adjust it later.
    booking = Booking.objects.create(
        booking_type='room',
        guest_name=guest_name,
        guest_phone=guest_phone,
        guest_id_no=guest_id_no,
        room_type=room_type,
        check_in=check_in,
        check_out=check_out,
        amount=room_type.price_min,
        status='pending',
        source='online',
    )
    return JsonResponse({'success': True, 'booking_id': booking.id})


@require_POST
def submit_order(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    customer_name = data.get('customer_name', '').strip()
    customer_phone = data.get('customer_phone', '').strip()
    notes = data.get('notes', '').strip()
    items = data.get('items', [])
    room_number = data.get('room_number', '').strip()

    if not customer_name or not customer_phone or not items:
        return JsonResponse({'error': 'Name, phone, and at least one item are required.'}, status=400)

    room_booking = None
    if room_number:
        room_booking = Booking.objects.filter(
            assigned_room__number=room_number, status='checked_in'
        ).first()
        if not room_booking:
            return JsonResponse({'error': f'No checked-in guest found in Room {room_number}. Check the room number.'}, status=400)

    unavailable = []
    for item in items:
        try:
            menu_item = MenuItem.objects.select_related('stock_item').get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        if not menu_item.is_in_stock:
            unavailable.append(menu_item.name)

    if unavailable:
        return JsonResponse({'error': f"Sorry, these items just sold out: {', '.join(unavailable)}. Please remove them and try again."}, status=409)

    order = Order.objects.create(
        customer_name=customer_name,
        customer_phone=customer_phone,
        notes=notes,
        status='pending',
        room_booking=room_booking,
    )
    for item in items:
        try:
            menu_item = MenuItem.objects.get(id=item['id'], is_available=True)
        except (MenuItem.DoesNotExist, KeyError):
            continue
        tier = item.get('tier') if item.get('tier') in ('regular', 'vip') else 'regular'
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            tier=tier,
            quantity=max(int(item.get('qty', 1)), 1),
        )

    return JsonResponse({'success': True, 'order_id': order.id})

def contact(request):
    session_email = request.session.get('contact_email')
    conversation = None
    messages = []
    if session_email:
        conversation = Conversation.objects.filter(email=session_email).first()
        if conversation:
            messages = list(conversation.messages.values('sender', 'body', 'created_at'))
    return render(request, 'public_site/contact.html', {
        'session_email': session_email,
        'messages': messages,
    })
    
@require_POST
def send_verification_code(request):
    data = json.loads(request.body)
    email = data.get('email', '').strip().lower()
    if not email or '@' not in email:
        return JsonResponse({'error': 'Please enter a valid email address.'}, status=400)

    code = str(random.randint(100000, 999999))
    EmailVerification.objects.create(email=email, code=code)

    send_mail(
        subject="Your Queens Garden Hotel verification code",
        message=(
            f"Your verification code is: {code}\n\n"
            f"This code expires in 10 minutes.\n\n"
            f"Queens Garden Hotel"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    return JsonResponse({'success': True})


@require_POST
def verify_code(request):
    data = json.loads(request.body)
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    name = data.get('name', '').strip()

    verification = EmailVerification.objects.filter(
        email=email, code=code, is_used=False
    ).order_by('-created_at').first()

    if not verification:
        return JsonResponse({'error': 'Invalid code.'}, status=400)
    if verification.is_expired():
        return JsonResponse({'error': 'That code has expired. Request a new one.'}, status=400)

    verification.is_used = True
    verification.save(update_fields=['is_used'])

    conversation, created = Conversation.objects.get_or_create(email=email)
    if name and not conversation.name:
        conversation.name = name
        conversation.save(update_fields=['name'])

    request.session['contact_email'] = email
    request.session.set_expiry(60 * 60 * 24 * 30)  # keep them logged in for 30 days on this browser

    messages = list(conversation.messages.values('sender', 'body', 'created_at'))
    return JsonResponse({'success': True, 'messages': messages})


def get_messages(request):
    email = request.session.get('contact_email')
    if not email:
        return JsonResponse({'error': 'Not verified.'}, status=401)
    conversation = Conversation.objects.filter(email=email).first()
    if not conversation:
        return JsonResponse({'messages': []})
    messages = list(conversation.messages.values('sender', 'body', 'created_at'))
    return JsonResponse({'messages': messages})


@require_POST
def send_message(request):
    email = request.session.get('contact_email')
    if not email:
        return JsonResponse({'error': 'Not verified.'}, status=401)

    data = json.loads(request.body)
    body = data.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Message cannot be empty.'}, status=400)

    conversation, _ = Conversation.objects.get_or_create(email=email)
    ChatMessage.objects.create(conversation=conversation, sender='guest', body=body)
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=['last_message_at'])

    return JsonResponse({'success': True})


def contact_logout(request):
    request.session.pop('contact_email', None)
    return JsonResponse({'success': True})