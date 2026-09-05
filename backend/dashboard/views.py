from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .permissions import MODULES, get_allowed_modules, has_full_access
from .decorators import staff_module_required

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from .models import GateLog
from .forms import GateEntryForm, GateEditForm
from django.core.paginator import Paginator

from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML

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
    return render(request, 'dashboard/module_placeholder.html', {'module_label': 'Reception'})


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