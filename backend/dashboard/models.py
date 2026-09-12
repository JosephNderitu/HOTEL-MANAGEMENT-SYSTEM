from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
import uuid


class StaffProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_profile')
    national_id = models.CharField(
        max_length=20, unique=True,
        help_text="National ID or Passport number. Used to identify this staff member on any record they handle."
    )
    phone = models.CharField(max_length=20, blank=True)
    department_note = models.CharField(max_length=100, blank=True, help_text="Optional, e.g. 'Night shift', 'Gate 2'")
    is_active_staff = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.national_id})"
    
class GateLog(models.Model):
    STATUS_CHOICES = [
        ('inside', 'Inside'),
        ('exited', 'Exited'),
    ]
    guest_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, blank=True)
    vehicle_make = models.CharField(max_length=100, blank=True)
    plate_number = models.CharField(max_length=20)
    purpose = models.CharField(max_length=150, blank=True, help_text="e.g. Visiting guest, Delivery, Meeting")
    parking_spot = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='inside')
    entry_time = models.DateTimeField(auto_now_add=True)
    exit_time = models.DateTimeField(null=True, blank=True)

    logged_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='gate_entries_logged'
    )
    logged_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='gate_exits_logged'
    )

    class Meta:
        ordering = ['-entry_time']

    @property
    def duration_display(self):
        if self.exit_time and self.entry_time:
            delta = self.exit_time - self.entry_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            return f"{hours}h {minutes}m"
        return "—"

    def __str__(self):
        return f"{self.guest_name} — {self.plate_number} ({self.get_status_display()})"


class Payment(models.Model):
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('bank_equity', 'Bank - Equity'),
        ('bank_family', 'Bank - Family'),
        ('bank_coop', 'Bank - Cooperative'),
        ('swipe', 'Swipe'),
    ]
    booking = models.ForeignKey('public_site.Booking', on_delete=models.PROTECT, null=True, blank=True, related_name='payments')
    order = models.ForeignKey('public_site.Order', on_delete=models.PROTECT, null=True, blank=True, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True, help_text="M-Pesa code, bank reference, etc (optional)")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payments_received')
    settlement_group = models.UUIDField(null=True, blank=True, db_index=True, help_text="Groups payments made together as one settlement, e.g. room + all room service in one go.")
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if not self.booking_id and not self.order_id:
            raise ValidationError("A payment must be linked to either a booking or an order.")
        if self.booking_id and self.order_id:
            raise ValidationError("A payment can't be linked to both a booking and an order.")

    def __str__(self):
        target = self.booking or self.order
        return f"KSh {self.amount} — {target} ({self.get_method_display()})"