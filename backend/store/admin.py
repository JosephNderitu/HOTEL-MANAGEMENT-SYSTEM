from django.contrib import admin
from .models import *
    
@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'unit', 'quantity_on_hand', 'reorder_level', 'linked_menu_item', 'is_active')
    list_editable = ('reorder_level', 'is_active')
    list_filter = ('department', 'is_active')
    search_fields = ('name',)
    autocomplete_fields = ('linked_menu_item',)


class DisbursementPurchaseInline(admin.TabularInline):
    model = DisbursementPurchase
    extra = 0
    readonly_fields = ('stock_item', 'quantity', 'unit_cost', 'receipt_reference', 'recorded_by', 'recorded_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Disbursement)
class DisbursementAdmin(admin.ModelAdmin):
    list_display = ('department', 'purpose', 'amount', 'status', 'total_spent_display', 'variance_display', 'requested_by', 'created_at')
    list_filter = ('department', 'status')
    readonly_fields = ('department', 'purpose', 'amount', 'status', 'requested_by', 'approved_by', 'disbursed_by', 'created_at', 'approved_at', 'disbursed_at', 'reconciled_at')
    inlines = [DisbursementPurchaseInline]

    @admin.display(description='Total Spent')
    def total_spent_display(self, obj):
        return f"KSh {obj.total_spent:,.0f}"

    @admin.display(description='Variance')
    def variance_display(self, obj):
        return f"KSh {obj.variance:,.0f}"

    def has_add_permission(self, request):
        return False


@admin.register(WastageLog)
class WastageLogAdmin(admin.ModelAdmin):
    list_display = ('stock_item', 'quantity', 'reason', 'logged_by', 'logged_at')
    date_hierarchy = 'logged_at'
    readonly_fields = ('stock_item', 'quantity', 'reason', 'logged_by', 'logged_at')

    def has_add_permission(self, request):
        return False


class DailyUsageItemInline(admin.TabularInline):
    model = DailyUsageItem
    verbose_name = "Logged Usage Item"
    verbose_name_plural = "Logged Usage Items"
    extra = 0
    readonly_fields = (
        'stock_item', 'custom_name', 'custom_unit', 'quantity', 
        'added_by', 'added_at', 'is_locked', 'confirmed_by', 'confirmed_at'
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyUsageLog)
class DailyUsageLogAdmin(admin.ModelAdmin):
    verbose_name = "Daily Department Usage Log"
    verbose_name_plural = "Daily Department Usage Logs"
    
    # Only use fields that actually exist on DailyUsageLog
    list_display = ('department', 'date', 'total_items_logged', 'created_at')
    list_filter = ('department', 'date')
    date_hierarchy = 'date'
    inlines = [DailyUsageItemInline]

    @admin.display(description='Total Items Logged')
    def total_items_logged(self, obj):
        return obj.items.count()