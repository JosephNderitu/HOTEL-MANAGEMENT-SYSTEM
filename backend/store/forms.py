from django import forms
from .models import DisbursementPurchase, StockItem

FIELD_CLASS = 'w-full border border-[#0B6B3A] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0B6B3A]/20'


class DisbursementRequestForm(forms.Form):
    department = forms.ChoiceField(choices=StockItem.DEPARTMENT_CHOICES, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    purpose = forms.CharField(widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Weekly bar restock'}))
    amount = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Amount requested'}))


class QuickPurchaseForm(forms.Form):
    mode = forms.ChoiceField(choices=[('existing', 'existing'), ('new', 'new')], widget=forms.HiddenInput())

    # Existing item path
    stock_item = forms.ModelChoiceField(queryset=StockItem.objects.none(), required=False,
        widget=forms.Select(attrs={'class': FIELD_CLASS}))

    # New item path
    new_item_name = forms.CharField(required=False,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Bathing soap, Paprika, Gordons Gin'}))
    new_item_unit = forms.ChoiceField(choices=StockItem.UNIT_CHOICES, required=False,
        widget=forms.Select(attrs={'class': FIELD_CLASS}))
    reorder_level = forms.DecimalField(required=False,
        widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Reorder level (optional)'}))

    add_to_menu = forms.BooleanField(required=False)
    menu_category = forms.CharField(required=False,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Spirits, Main Course'}))
    menu_regular_price = forms.DecimalField(required=False,
        widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Guest price (regular)'}))
    menu_vip_price = forms.DecimalField(required=False,
        widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'VIP price (optional)'}))
    menu_description = forms.CharField(required=False,
        widget=forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2, 'placeholder': 'Short description for guests (optional)'}))

    # Shared, always required
    quantity = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Quantity purchased'}))
    unit_cost = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Cost per unit'}))
    receipt_reference = forms.CharField(required=False,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Receipt number (optional)'}))

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.department = department
        if department:
            self.fields['stock_item'].queryset = StockItem.objects.filter(department=department, is_active=True)

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')
        if mode == 'existing' and not cleaned.get('stock_item'):
            raise forms.ValidationError("Select an existing stock item, or switch to 'New Item'.")
        if mode == 'new':
            if not cleaned.get('new_item_name'):
                raise forms.ValidationError("Enter a name for the new item.")
            if not cleaned.get('new_item_unit'):
                raise forms.ValidationError("Select a unit for the new item.")
            if cleaned.get('add_to_menu') and not cleaned.get('menu_regular_price'):
                raise forms.ValidationError("Enter a guest price to add this item to the menu.")
        return cleaned