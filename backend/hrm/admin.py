# NEW — the entire file
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from .models import *

# ==============================================================================
# SHIFTS & SCHEDULING
# ==============================================================================


@admin.register(StaffWeeklyOff)
class StaffWeeklyOffAdmin(admin.ModelAdmin):
    list_display = ('staff', 'off_days_display')
    autocomplete_fields = ('staff',)
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name')

    def off_days_display(self, obj):
        return ', '.join(obj.weekday_labels()) or '—'
    off_days_display.short_description = 'Off days'


@admin.register(ShiftSwapRequest)
class ShiftSwapRequestAdmin(admin.ModelAdmin):
    list_display = ('requested_by', 'mode', 'start_date', 'end_date', 'days_count', 'status', 'reviewed_by')
    list_filter = ('mode', 'status')
    date_hierarchy = 'start_date'
    search_fields = ('requested_by__username', 'requested_by__first_name', 'requested_by__last_name')
    autocomplete_fields = ('requested_by', 'trade_with')
    readonly_fields = ('reviewed_by', 'reviewed_at', 'created_at')
    actions = ['approve_requests', 'reject_requests']

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj and obj.status != 'pending':
            ro += ['requested_by', 'mode', 'start_date', 'end_date', 'requested_shift', 'trade_with', 'reason', 'status']
        return ro

    @admin.action(description="Approve selected swap requests")
    def approve_requests(self, request, queryset):
        count = 0
        for swap in queryset.filter(status='pending'):
            swap.apply(reviewed_by=request.user)
            count += 1
        self.message_user(request, f"Approved {count} swap request(s).")

    @admin.action(description="Reject selected swap requests")
    def reject_requests(self, request, queryset):
        updated = queryset.filter(status='pending').update(status='rejected', reviewed_by=request.user, reviewed_at=timezone.now())
        self.message_user(request, f"Rejected {updated} swap request(s).")

@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_time', 'end_time', 'grace_minutes')
    search_fields = ('name',)


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ('staff', 'shift', 'date', 'status', 'assigned_by')
    list_filter = ('shift', 'status', 'date')
    date_hierarchy = 'date'
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name')
    autocomplete_fields = ('staff',)
    readonly_fields = ('assigned_by', 'created_at')

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.assigned_by = request.user  # who scheduled it is fixed at creation, never reassignable
        super().save_model(request, obj, form, change)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """
    Attendance is proof-of-presence data captured by the staff app at the moment
    of clock-in/out — GPS, a geofence check, a photo. Letting anyone type over
    that afterward defeats the entire point of collecting it, so this admin is
    deliberately view + delete only: no add, no edit. A wrong record gets
    deleted and the staff member re-clocks in through the real flow, rather
    than being "corrected" by hand.
    """
    list_display = (
        'staff', 'date', 'shift_assignment', 'clock_in_time', 'clock_out_time',
        'hours_worked', 'is_late', 'late_minutes', 'geofence_badge',
    )
    list_filter = ('date', 'is_late', 'clock_in_within_geofence', 'clock_out_within_geofence')
    date_hierarchy = 'date'
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name')
    autocomplete_fields = ('staff', 'shift_assignment')
    readonly_fields = [f.name for f in AttendanceRecord._meta.fields] + ['hours_worked']

    def geofence_badge(self, obj):
        if obj.clock_in_time and not obj.clock_in_within_geofence:
            return format_html('<span style="color:#b91c1c;font-weight:bold;">Clock-in outside geofence</span>')
        if obj.clock_out_time and not obj.clock_out_within_geofence:
            return format_html('<span style="color:#b91c1c;font-weight:bold;">Clock-out outside geofence</span>')
        return format_html('<span style="color:#0B6B3A;">OK</span>')
    geofence_badge.short_description = 'Geofence'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ==============================================================================
# LEAVE
# ==============================================================================

class LeaveBalanceInline(admin.TabularInline):
    """Only a handful of leave TYPES exist (Annual, Sick...); balances are the
    per-staff, per-year numbers against each type. Reviewing them from the type
    page is how HR actually thinks about it — 'who has how much Annual Leave
    left this year' — rather than a flat, unfiltered list of every balance."""
    model = LeaveBalance
    extra = 0
    fields = ('staff', 'year', 'allocated_days', 'used_days', 'remaining_days_display')
    readonly_fields = ('remaining_days_display',)
    autocomplete_fields = ('staff',)

    def remaining_days_display(self, obj):
        return obj.remaining_days if obj.pk else '—'
    remaining_days_display.short_description = 'Remaining'


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_days_per_year')
    inlines = [LeaveBalanceInline]


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('staff', 'leave_type', 'start_date', 'end_date', 'days_count_display', 'status', 'reviewed_by')
    list_filter = ('status', 'leave_type')
    date_hierarchy = 'start_date'
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name')
    autocomplete_fields = ('staff',)
    readonly_fields = ('reviewed_by', 'reviewed_at', 'created_at', 'days_count_display')
    actions = ['approve_requests', 'reject_requests']

    def days_count_display(self, obj):
        return obj.days_count
    days_count_display.short_description = 'Days requested'

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj and obj.status != 'pending':
            # A decision has been made — lock the request itself so the history stays honest.
            # Reviewers use the actions below rather than editing a decided request in place.
            ro += ['staff', 'leave_type', 'start_date', 'end_date', 'reason', 'status']
        return ro

    @admin.action(description="Approve selected leave requests")
    def approve_requests(self, request, queryset):
        updated = queryset.filter(status='pending').update(
            status='approved', reviewed_by=request.user, reviewed_at=timezone.now()
        )
        self.message_user(request, f"Approved {updated} leave request(s).")

    @admin.action(description="Reject selected leave requests")
    def reject_requests(self, request, queryset):
        updated = queryset.filter(status='pending').update(
            status='rejected', reviewed_by=request.user, reviewed_at=timezone.now()
        )
        self.message_user(request, f"Rejected {updated} leave request(s).")


# ==============================================================================
# PAYROLL
# ==============================================================================

FINANCIAL_FIELDS = (
    'basic_salary', 'allowances_total', 'allowances_notes',
    'deductions_total', 'deductions_notes', 'overtime_hours', 'overtime_rate',
)


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ('staff', 'period_month', 'period_year', 'gross_pay_display', 'net_pay_display', 'status')
    list_filter = ('status', 'period_year', 'period_month')
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name')
    autocomplete_fields = ('staff',)
    readonly_fields = ('generated_by', 'generated_at', 'overtime_pay_display', 'gross_pay_display', 'net_pay_display')
    actions = ['reopen_as_draft']

    def gross_pay_display(self, obj):
        return f"KSh {obj.gross_pay:,.2f}" if obj.pk else '—'
    gross_pay_display.short_description = 'Gross pay'

    def net_pay_display(self, obj):
        return f"KSh {obj.net_pay:,.2f}" if obj.pk else '—'
    net_pay_display.short_description = 'Net pay'

    def overtime_pay_display(self, obj):
        return f"KSh {obj.overtime_pay:,.2f}" if obj.pk else '—'
    overtime_pay_display.short_description = 'Overtime pay'

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj and obj.status == 'finalized':
            # A mistake here is a real money discrepancy after payout, not a typo to quietly fix.
            ro += list(FINANCIAL_FIELDS) + ['staff', 'period_month', 'period_year', 'status']
        return ro

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.generated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Reopen as draft (superuser only)")
    def reopen_as_draft(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "Only a superuser can reopen a finalized payroll record.", level=messages.ERROR)
            return
        updated = queryset.filter(status='finalized').update(status='draft')
        self.message_user(request, f"Reopened {updated} record(s) for editing.")


# ==============================================================================
# PERFORMANCE
# ==============================================================================

REVIEW_CONTENT_FIELDS = ('overall_rating', 'strengths', 'areas_to_improve', 'goals')


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = ('staff', 'reviewer', 'review_period', 'overall_rating', 'status', 'created_at')
    list_filter = ('status', 'overall_rating')
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name', 'review_period')
    autocomplete_fields = ('staff',)
    readonly_fields = ('reviewer', 'created_at')

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj and obj.status == 'finalized':
            # A finalized review is the historical record of that period — don't let it be rewritten.
            ro += list(REVIEW_CONTENT_FIELDS) + ['status']
        return ro

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.reviewer = request.user
        super().save_model(request, obj, form, change)