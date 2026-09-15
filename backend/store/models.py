from django.db import models
from django.conf import settings


class StockItem(models.Model):
    DEPARTMENT_CHOICES = [
        ('kitchen', 'Kitchen'),
        ('bar', 'Bar'),
        ('housekeeping', 'Housekeeping'),
        ('front_desk', 'Front Desk'),
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
        return self.quantity_on_hand <= self.reorder_level

    def __str__(self):
        return f"{self.name} ({self.get_department_display()})"


class Disbursement(models.Model):
    DEPARTMENT_CHOICES = StockItem.DEPARTMENT_CHOICES
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('disbursed', 'Disbursed'),
        ('reconciled', 'Reconciled'),
    ]
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    purpose = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='requested')

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='disbursements_requested')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='disbursements_approved')
    disbursed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='disbursements_given')

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    @property
    def total_spent(self):
        return sum(p.total_cost for p in self.purchases.all())

    @property
    def variance(self):
        return self.amount - self.total_spent

    def __str__(self):
        return f"{self.get_department_display()} — KSh {self.amount} ({self.get_status_display()})"


class DisbursementPurchase(models.Model):
    disbursement = models.ForeignKey(Disbursement, on_delete=models.PROTECT, related_name='purchases')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name='purchase_records')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    receipt_reference = models.CharField(max_length=100, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases_recorded')
    recorded_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.stock_item.quantity_on_hand += self.quantity
            self.stock_item.last_unit_cost = self.unit_cost
            self.stock_item.save(update_fields=['quantity_on_hand', 'last_unit_cost'])

    def __str__(self):
        return f"{self.quantity} x {self.stock_item.name}"
    
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
    STATUS_CHOICES = [('draft', 'Draft'), ('confirmed', 'Confirmed')]
    department = models.CharField(max_length=20, choices=StockItem.DEPARTMENT_CHOICES)
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='usage_logs_confirmed')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('department', 'date')

    def __str__(self):
        return f"{self.get_department_display()} usage — {self.date} ({self.get_status_display()})"


class DailyUsageItem(models.Model):
    log = models.ForeignKey(DailyUsageLog, on_delete=models.CASCADE, related_name='items')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='daily_usage_records')
    custom_name = models.CharField(max_length=150, blank=True)
    custom_unit = models.CharField(max_length=10, choices=StockItem.UNIT_CHOICES, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='usage_items_added')
    added_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_name(self):
        return self.stock_item.name if self.stock_item else self.custom_name

    @property
    def display_unit(self):
        return self.stock_item.unit if self.stock_item else self.custom_unit

    def __str__(self):
        return f"{self.quantity} {self.display_unit} {self.display_name}"