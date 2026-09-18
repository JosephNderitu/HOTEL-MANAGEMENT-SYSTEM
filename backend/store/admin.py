from django.contrib import admin
from django.utils.html import format_html
from .models import StockItem, WastageLog, DailyUsageLog, DailyUsageItem, PurchaseLog, PurchaseReceipt


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'unit', 'quantity_on_hand', 'reorder_level', 'stock_badge', 'linked_menu_item', 'is_active')
    list_editable = ('reorder_level', 'is_active')
    list_filter = ('department', 'is_active')
    search_fields = ('name',)
    autocomplete_fields = ('linked_menu_item',)

    @admin.display(description='Status')
    def stock_badge(self, obj):
        colors = {'danger': '#dc2626', 'warning': '#C9A227', 'ok': '#0B6B3A'}
        labels = {'danger': 'OUT OF STOCK', 'warning': 'LOW STOCK', 'ok': 'OK'}
        c = colors[obj.stock_status]
        text = '#000' if obj.stock_status == 'warning' else '#fff'
        return format_html('<span style="background:{};color:{};padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:700;">{}</span>', c, text, labels[obj.stock_status])


class PurchaseReceiptInline(admin.TabularInline):
    model = PurchaseReceipt
    extra = 1
    fields = ('image', 'preview')
    readonly_fields = ('preview',)

    @admin.display(description='Preview')
    def preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width:70px;height:70px;object-fit:cover;border-radius:6px;" />', obj.image.url)
        return "—"


@admin.register(PurchaseLog)
class PurchaseLogAdmin(admin.ModelAdmin):
    list_display = ('purchased_at', 'department', 'stock_item', 'quantity', 'unit_cost', 'total_display', 'vat_display', 'purchased_by', 'receipt_count')
    list_filter = ('department', 'vat_inclusive')
    search_fields = ('stock_item__name', 'notes')
    date_hierarchy = 'purchased_at'
    autocomplete_fields = ('stock_item',)
    inlines = [PurchaseReceiptInline]

    @admin.display(description='Total (Incl. VAT)')
    def total_display(self, obj):
        return f"KSh {obj.total_cost:,.0f}"

    @admin.display(description='VAT')
    def vat_display(self, obj):
        return f"KSh {obj.vat_amount:,.0f}"

    @admin.display(description='Receipts')
    def receipt_count(self, obj):
        return obj.receipts.count()


@admin.register(WastageLog)
class WastageLogAdmin(admin.ModelAdmin):
    list_display = ('stock_item', 'quantity', 'reason', 'logged_by', 'logged_at')
    date_hierarchy = 'logged_at'
    readonly_fields = ('stock_item', 'quantity', 'reason', 'logged_by', 'logged_at')

    def has_add_permission(self, request):
        return False


class DailyUsageItemInline(admin.TabularInline):
    model = DailyUsageItem
    extra = 0
    readonly_fields = ('stock_item', 'custom_name', 'custom_unit', 'quantity', 'added_by', 'added_at', 'is_locked', 'confirmed_by', 'confirmed_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyUsageLog)
class DailyUsageLogAdmin(admin.ModelAdmin):
    list_display = ('department', 'date', 'total_items_logged', 'created_at')
    list_filter = ('department', 'date')
    date_hierarchy = 'date'
    inlines = [DailyUsageItemInline]

    @admin.display(description='Items Logged')
    def total_items_logged(self, obj):
        return obj.items.count()