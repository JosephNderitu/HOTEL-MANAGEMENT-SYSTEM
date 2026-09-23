from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .permissions import MODULES, can_access, can_manage_hrm


def staff_module_required(module_code):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            if not request.user.is_staff or not can_access(request.user, module_code):
                return render(
                    request,
                    'dashboard/restricted.html',
                    {'module_label': MODULES[module_code]['label']},
                    status=403,
                )
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator

def hrm_manager_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not can_manage_hrm(request.user):
            return render(request, 'dashboard/restricted.html', {'module_label': 'HRM Management'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped