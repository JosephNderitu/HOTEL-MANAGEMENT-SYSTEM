from datetime import datetime, date as date_cls, timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone


from django.core.exceptions import ValidationError


class Shift(models.Model):
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveIntegerField(default=10)

    @property
    def shift_kind(self):
        """Rough day/night classifier for calendar colour-coding."""
        hour = self.start_time.hour
        return 'night' if hour >= 18 or hour < 5 else 'day'
    
    def __str__(self):
        return f"{self.name} ({self.start_time}–{self.end_time})"


class ShiftAssignment(models.Model):
    STATUS_CHOICES = [('scheduled', 'Scheduled'), ('swap_requested', 'Swap Requested'), ('swapped', 'Swapped'), ('cancelled', 'Cancelled')]
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shift_assignments')
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name='assignments')
    date = models.DateField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='scheduled')
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='shifts_assigned')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('staff', 'date')
        
    @staticmethod
    def is_staff_on_leave(staff, day):
        return LeaveRequest.objects.filter(
            staff=staff, status='approved', start_date__lte=day, end_date__gte=day
        ).exists()

    @staticmethod
    def is_weekly_off(staff, day):
        try:
            off = staff.weekly_off
        except StaffWeeklyOff.DoesNotExist:
            return False
        return day.weekday() in off.weekdays

    def clean(self):
        if self.staff_id and self.date:
            if self.is_weekly_off(self.staff, self.date):
                raise ValidationError(f"{self.staff} is normally off on {self.date.strftime('%A')}s.")
            if self.is_staff_on_leave(self.staff, self.date):
                raise ValidationError(f"{self.staff} is on approved leave on {self.date}.")

    @classmethod
    def bulk_assign(cls, staff, shift, start_date, end_date, assigned_by):
        """Creates one row per day in the range, skipping weekly-off days,
        approved-leave days, and days already assigned. Returns (created, skipped)
        where skipped is a list of (date, reason) tuples for the confirmation screen."""
        created, skipped = [], []
        day = start_date
        while day <= end_date:
            if cls.is_weekly_off(staff, day):
                skipped.append((day, 'weekly off'))
            elif cls.is_staff_on_leave(staff, day):
                skipped.append((day, 'on approved leave'))
            elif cls.objects.filter(staff=staff, date=day).exists():
                skipped.append((day, 'already assigned'))
            else:
                created.append(cls.objects.create(staff=staff, shift=shift, date=day, assigned_by=assigned_by))
            day += timedelta(days=1)
        return created, skipped

    def __str__(self):
        return f"{self.staff} — {self.shift.name} on {self.date}"


class ShiftSwapRequest(models.Model):
    MODE_CHOICES = [('self', 'Change My Shift'), ('trade', 'Trade With Colleague')]
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='swap_requests_made')
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='self')
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(default=timezone.now)
    requested_shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name='requested_in_swaps', null=True, blank=True)
    trade_with = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='swap_requests_received')
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='swap_requests_reviewed')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    @property
    def days_count(self):
        return (self.end_date - self.start_date).days + 1

    def clean(self):
        if self.mode == 'self' and not self.requested_shift_id:
            raise ValidationError("Pick the shift you want to move to.")
        if self.mode == 'trade' and not self.trade_with_id:
            raise ValidationError("Pick the colleague you want to trade with.")
        if self.end_date < self.start_date:
            raise ValidationError("End date can't be before start date.")

    def apply(self, reviewed_by):
        """Approves the request and mutates the underlying ShiftAssignment rows
        for every day in the range."""
        day = self.start_date
        while day <= self.end_date:
            if self.mode == 'self':
                assignment, _ = ShiftAssignment.objects.get_or_create(
                    staff=self.requested_by, date=day,
                    defaults={'shift': self.requested_shift, 'assigned_by': reviewed_by},
                )
                assignment.shift = self.requested_shift
                assignment.status = 'swapped'
                assignment.save(update_fields=['shift', 'status'])
            else:
                mine = ShiftAssignment.objects.filter(staff=self.requested_by, date=day).first()
                theirs = ShiftAssignment.objects.filter(staff=self.trade_with, date=day).first()
                if mine and theirs:
                    mine.shift, theirs.shift = theirs.shift, mine.shift
                    mine.status = theirs.status = 'swapped'
                    mine.save(update_fields=['shift', 'status'])
                    theirs.save(update_fields=['shift', 'status'])
            day += timedelta(days=1)
        self.status = 'approved'
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    def __str__(self):
        return f"{self.requested_by} — {self.get_mode_display()} ({self.start_date} to {self.end_date})"


class StaffWeeklyOff(models.Model):
    """A staff member's standing weekly day(s) off — one row per staff,
    weekdays held as a list. HR edits it directly when a pattern changes."""
    WEEKDAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]
    staff = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='weekly_off')
    weekdays = models.JSONField(default=list, blank=True)

    def weekday_labels(self):
        labels = dict(self.WEEKDAY_CHOICES)
        return [labels[d] for d in sorted(self.weekdays)]

    def __str__(self):
        return f"{self.staff} — {', '.join(self.weekday_labels()) or 'no off days set'}"
    

class AttendanceRecord(models.Model):
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(default=timezone.localdate)
    shift_assignment = models.ForeignKey(ShiftAssignment, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance')

    clock_in_time = models.DateTimeField(null=True, blank=True)
    clock_in_lat = models.FloatField(null=True, blank=True)
    clock_in_lng = models.FloatField(null=True, blank=True)
    clock_in_photo = models.ImageField(upload_to='attendance_photos/', null=True, blank=True)
    clock_in_within_geofence = models.BooleanField(default=False)

    clock_out_time = models.DateTimeField(null=True, blank=True)
    clock_out_lat = models.FloatField(null=True, blank=True)
    clock_out_lng = models.FloatField(null=True, blank=True)
    clock_out_photo = models.ImageField(upload_to='attendance_photos/', null=True, blank=True)
    clock_out_within_geofence = models.BooleanField(default=False)

    is_late = models.BooleanField(default=False)
    late_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('staff', 'date')

    @property
    def hours_worked(self):
        if self.clock_in_time and self.clock_out_time:
            return round((self.clock_out_time - self.clock_in_time).total_seconds() / 3600, 2)
        return None

    def __str__(self):
        return f"{self.staff} — {self.date}"


class LeaveType(models.Model):
    name = models.CharField(max_length=50)
    default_days_per_year = models.PositiveIntegerField(default=21)

    def __str__(self):
        return self.name


class LeaveBalance(models.Model):
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='balances')
    year = models.PositiveIntegerField(default=timezone.localdate().year)
    allocated_days = models.PositiveIntegerField(default=0)
    used_days = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('staff', 'leave_type', 'year')

    @property
    def remaining_days(self):
        return self.allocated_days - self.used_days


class LeaveRequest(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')]
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='requests')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    rejection_reason = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='leave_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def days_count(self):
        return (self.end_date - self.start_date).days + 1


class PayrollRecord(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('finalized', 'Finalized')]
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payroll_records')
    period_month = models.PositiveIntegerField()
    period_year = models.PositiveIntegerField()
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2)
    allowances_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    allowances_notes = models.CharField(max_length=200, blank=True)
    deductions_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions_notes = models.CharField(max_length=200, blank=True)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payrolls_generated')
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('staff', 'period_month', 'period_year')

    @property
    def overtime_pay(self):
        return self.overtime_hours * self.overtime_rate

    @property
    def gross_pay(self):
        return self.basic_salary + self.allowances_total + self.overtime_pay

    @property
    def net_pay(self):
        return self.gross_pay - self.deductions_total


class PerformanceReview(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('finalized', 'Finalized')]
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='performance_reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reviews_conducted')
    review_period = models.CharField(max_length=100)
    overall_rating = models.PositiveIntegerField()
    strengths = models.TextField(blank=True)
    areas_to_improve = models.TextField(blank=True)
    goals = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)