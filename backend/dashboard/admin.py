from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import StaffProfile, EmploymentAction, GateLog, Payment


class StaffProfileInline(admin.StackedInline):
    """Full profile, editable here for corrections — but changes made directly
    in admin do NOT create an EmploymentAction log entry. Use the HRM app's
    promote/demote/suspend/terminate flows for anything that should be
    recorded in the staff member's history; use this inline only for fixing
    typos or filling in missing details."""
    model = StaffProfile
    can_delete = False
    fk_name = 'user'
    extra = 0
    fields = (
        'national_id', 'phone', 'position', 'department', 'department_note',
        'date_joined_role', 'employment_status', 'pay_type', 'pay_rate',
        'emergency_contact_name', 'emergency_contact_phone', 'is_active_staff',
    )


class CustomUserAdmin(UserAdmin):
    inlines = [StaffProfileInline]
    list_display = UserAdmin.list_display + ('national_id_display', 'department_display', 'status_display', 'group_list')
    list_filter = UserAdmin.list_filter + ('staff_profile__department', 'staff_profile__employment_status')

    @admin.display(description='National ID')
    def national_id_display(self, obj):
        return getattr(getattr(obj, 'staff_profile', None), 'national_id', '—')

    @admin.display(description='Department')
    def department_display(self, obj):
        profile = getattr(obj, 'staff_profile', None)
        return profile.get_department_display() if profile and profile.department else '—'

    @admin.display(description='Status')
    def status_display(self, obj):
        profile = getattr(obj, 'staff_profile', None)
        return profile.get_employment_status_display() if profile else '—'

    @admin.display(description='Roles')
    def group_list(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or '—'


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


@admin.register(EmploymentAction)
class EmploymentActionAdmin(admin.ModelAdmin):
    """This is an audit trail — history should never be hand-edited or backdated,
    so this admin is deliberately view + delete only (delete reserved for a
    genuine data-entry mistake, superuser only)."""
    list_display = ('staff', 'action', 'previous_position', 'new_position', 'performed_by', 'created_at')
    list_filter = ('action',)
    date_hierarchy = 'created_at'
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name', 'note')
    readonly_fields = [f.name for f in EmploymentAction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(GateLog)
class GateLogAdmin(admin.ModelAdmin):
    list_display = ('guest_name', 'plate_number', 'status', 'entry_time', 'exit_time', 'logged_in_by', 'logged_out_by')
    list_filter = ('status',)
    search_fields = ('guest_name', 'plate_number', 'phone_number')
    date_hierarchy = 'entry_time'
    readonly_fields = ('logged_in_by', 'logged_out_by', 'entry_time', 'exit_time')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'target_display', 'amount', 'method', 'received_by', 'created_at')
    list_filter = ('method',)
    search_fields = ('reference',)
    date_hierarchy = 'created_at'
    readonly_fields = ('booking', 'order', 'amount', 'method', 'reference', 'received_by', 'created_at')

    @admin.display(description='For')
    def target_display(self, obj):
        return obj.booking or obj.order

    def has_add_permission(self, request):
        return False