from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import StaffProfile


class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    can_delete = False
    fk_name = 'user'
    extra = 0
    fields = ('national_id', 'phone', 'department_note', 'is_active_staff')


class CustomUserAdmin(UserAdmin):
    inlines = [StaffProfileInline]
    list_display = UserAdmin.list_display + ('national_id_display', 'group_list')

    @admin.display(description='National ID')
    def national_id_display(self, obj):
        return getattr(getattr(obj, 'staff_profile', None), 'national_id', '—')

    @admin.display(description='Roles')
    def group_list(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or '—'


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

from .models import StaffProfile, GateLog


@admin.register(GateLog)
class GateLogAdmin(admin.ModelAdmin):
    list_display = ('guest_name', 'plate_number', 'status', 'entry_time', 'exit_time', 'logged_in_by', 'logged_out_by')
    list_filter = ('status',)
    search_fields = ('guest_name', 'plate_number', 'phone_number')
    date_hierarchy = 'entry_time'
    readonly_fields = ('logged_in_by', 'logged_out_by', 'entry_time', 'exit_time')