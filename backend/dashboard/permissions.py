FULL_ACCESS_GROUPS = {'Owner', 'General Manager'}

MODULES = {
    'reception': {
        'label': 'Reception',
        'icon': 'fa-bell-concierge',
        'url_name': 'dashboard:reception',
    },
    'rooms': {
        'label': 'Rooms',
        'icon': 'fa-bed',
        'url_name': 'dashboard:rooms',
    },
    'restaurant_kitchen': {
        'label': 'Restaurant & Kitchen',
        'icon': 'fa-utensils',
        'url_name': 'dashboard:restaurant_kitchen',
    },
    'bar': {
        'label': 'Bar',
        'icon': 'fa-martini-glass-citrus',
        'url_name': 'dashboard:bar',
    },
    'gate': {
        'label': 'Gate',
        'icon': 'fa-shield-halved',
        'url_name': 'dashboard:gate',
    },
    'hrm': {
        'label': 'HRM & Management',
        'icon': 'fa-users-gear',
        'url_name': 'dashboard:hrm',
    },
    'store': {
        'label': 'Store & Inventory',
        'icon': 'fa-boxes-stacked',
        'url_name': 'dashboard:store',
    },
}

# None means full access, handled separately via FULL_ACCESS_GROUPS
ROLE_MODULE_MAP = {
    'Front Office Manager': ['reception', 'rooms'],
    'Receptionist': ['reception', 'rooms'],
    'Housekeeping': ['rooms'],
    'Room Manager': ['rooms'],
    'Bar Manager': ['bar'],
    'Chef': ['restaurant_kitchen'],
    'Kitchen Staff': ['restaurant_kitchen'],
    'Security': ['gate'],
}

FRONT_DESK_CAPABLE_GROUPS = {'Front Office Manager', 'Receptionist', 'Room Manager'}

ROLE_RANK = {
    'Owner': 0,
    'General Manager': 1,
    'HR Manager': 2,
    'Front Office Manager': 3,
    'Room Manager': 3,
    'Bar Manager': 3,
}
DEFAULT_ROLE_RANK = 5

def has_full_access(user):
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=FULL_ACCESS_GROUPS).exists()


def get_allowed_modules(user):
    if not user.is_authenticated:
        return set()
    if has_full_access(user):
        return set(MODULES.keys())
    allowed = set()
    for group_name in user.groups.values_list('name', flat=True):
        mods = ROLE_MODULE_MAP.get(group_name)
        if mods:
            allowed.update(mods)
    allowed.add('hrm')  # every staff member gets self-service HRM access (clock in, own leave, own payslips)
    return allowed


def can_access(user, module_code):
    return module_code in get_allowed_modules(user)

def can_manage_bookings_from_rooms(user):
    if has_full_access(user):
        return True
    return user.groups.filter(name__in=FRONT_DESK_CAPABLE_GROUPS).exists()

HRM_MANAGER_GROUPS = FULL_ACCESS_GROUPS | {'HR Manager'}

def can_manage_hrm(user):
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=HRM_MANAGER_GROUPS).exists()

def get_role_rank(user):
    if user.is_superuser:
        return 0
    names = list(user.groups.values_list('name', flat=True))
    if not names:
        return DEFAULT_ROLE_RANK
    return min(ROLE_RANK.get(n, DEFAULT_ROLE_RANK) for n in names)

def can_manage_target(acting_user, target_user):
    """An acting user can manage anyone ranked strictly below them,
    unless they have full access (Owner/GM), who can manage anyone."""
    if has_full_access(acting_user):
        return True
    if acting_user.id == target_user.id:
        return False
    return get_role_rank(acting_user) < get_role_rank(target_user)