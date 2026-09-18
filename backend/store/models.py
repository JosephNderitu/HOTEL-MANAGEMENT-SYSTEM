from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from public_site.models import compress_image_if_needed

VAT_RATE = Decimal('0.16')


class StockItem(models.Model):
    DEPARTMENT_CHOICES = [
        ('kitchen', 'Kitchen / Restaurant'),
        ('bar', 'Bar'),
        ('housekeeping', 'Housekeeping / Rooms'),
        ('front_desk', 'Front Desk / Reception'),
        ('conference', 'Conference & Events'),
        ('general', 'General / Other'),
    ]
    UNIT_CHOICES = [
        ('kg', 'Kilogram'), ('g', 'Gram'), ('l', 'Litre'), ('ml', 'Millilitre'),
        ('piece', 'Piece'), ('pack', 'Pack'), ('box', 'Box'), ('bottle', 'Bottle'),
    ]
    name = models.CharField(max_length=150)
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES)
    quantity_on_hand = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_level = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    last_unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    linked_menu_item = models.OneToOneField(
        'public_site.MenuItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_item',
        help_text="If guests can order this, link it to its menu listing so restocking never touches the menu."
    )

    @property
    def is_low_stock(self):
        return 0 < self.quantity_on_hand <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.quantity_on_hand <= 0

    @property
    def stock_status(self):
        if self.is_out_of_stock:
            return 'danger'
        if self.is_low_stock:
            return 'warning'
        return 'ok'

    def __str__(self):
        return f"{self.name} ({self.get_department_display()})"


class WastageLog(models.Model):
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name='wastage_records')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200)
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='wastage_logged')
    logged_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.stock_item.quantity_on_hand -= self.quantity
            self.stock_item.save(update_fields=['quantity_on_hand'])

    def __str__(self):
        return f"{self.quantity} {self.stock_item.unit} {self.stock_item.name} wasted — {self.reason}"


class DailyUsageLog(models.Model):
    department = models.CharField(max_length=20, choices=StockItem.DEPARTMENT_CHOICES)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('department', 'date')

    def __str__(self):
        return f"{self.get_department_display()} usage — {self.date}"


class DailyUsageItem(models.Model):
    log = models.ForeignKey(DailyUsageLog, on_delete=models.CASCADE, related_name='items')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='daily_usage_records')
    custom_name = models.CharField(max_length=150, blank=True)
    custom_unit = models.CharField(max_length=10, choices=StockItem.UNIT_CHOICES, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='usage_items_added')
    added_at = models.DateTimeField(auto_now_add=True)
    is_locked = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='usage_items_confirmed')
    confirmed_at = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self):
        return self.stock_item.name if self.stock_item else self.custom_name

    @property
    def display_unit(self):
        return self.stock_item.unit if self.stock_item else self.custom_unit

    def __str__(self):
        return f"{self.quantity} {self.display_unit} {self.display_name}"


# ---------------------------------------------------------------------------
# Purchasing — the Store Manager is the sole buyer, fully accountable.
# No request/approve/disburse cycle: they log what they bought, when, for
# which department, with receipts attached, done.
# ---------------------------------------------------------------------------

class PurchaseLog(models.Model):
    DEPARTMENT_CHOICES = StockItem.DEPARTMENT_CHOICES

    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name='purchase_logs')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price paid per unit")
    vat_inclusive = models.BooleanField(default=True, help_text="Is the unit cost above already inclusive of 16% VAT?")
    notes = models.CharField(max_length=200, blank=True)
    purchased_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases_made')
    purchased_at = models.DateTimeField(default=timezone.now, help_text="When the purchase actually happened")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-purchased_at']

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    @property
    def amount_excl_vat(self):
        if self.vat_inclusive:
            return (self.total_cost / (1 + VAT_RATE)).quantize(Decimal('0.01'))
        return self.total_cost

    @property
    def vat_amount(self):
        return (self.amount_excl_vat * VAT_RATE).quantize(Decimal('0.01'))

    @property
    def amount_incl_vat(self):
        return self.amount_excl_vat + self.vat_amount

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.stock_item.quantity_on_hand += self.quantity
            self.stock_item.last_unit_cost = self.unit_cost
            self.stock_item.save(update_fields=['quantity_on_hand', 'last_unit_cost'])

    def __str__(self):
        return f"{self.quantity} x {self.stock_item.name} — KSh {self.total_cost:,.0f}"


class PurchaseReceipt(models.Model):
    purchase = models.ForeignKey(PurchaseLog, on_delete=models.CASCADE, related_name='receipts')
    image = models.ImageField(upload_to='purchase_receipts/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.image and hasattr(self.image, 'file'):
            self.image = compress_image_if_needed(self.image)
        super().save(*args, **kwargs)