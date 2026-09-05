from .permissions import MODULES, get_allowed_modules


def dashboard_nav(request):
    if not request.user.is_authenticated:
        return {}
    allowed = get_allowed_modules(request.user)
    nav_items = [
        {
            'code': code,
            'label': mod['label'],
            'icon': mod['icon'],
            'url_name': mod['url_name'],
            'allowed': code in allowed,
        }
        for code, mod in MODULES.items()
    ]
    return {'dashboard_nav_items': nav_items}