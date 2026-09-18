from decimal import Decimal
from django import forms
from .models import StockItem

FIELD_CLASS = 'w-full border border-[#0B6B3A] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0B6B3A]/20'


class PurchaseLogForm(forms.Form):
    mode = forms.ChoiceField(choices=[('existing', 'existing'), ('new', 'new')], widget=forms.HiddenInput())
    pricing_mode = forms.ChoiceField(choices=[('unit', 'unit'), ('total', 'total')], widget=forms.HiddenInput(), initial='unit')
    department = forms.ChoiceField(choices=StockItem.DEPARTMENT_CHOICES, widget=forms.Select(attrs={'class': FIELD_CLASS}))

    stock_item = forms.ModelChoiceField(queryset=StockItem.objects.none(), required=False,
        widget=forms.Select(attrs={'class': FIELD_CLASS}))

    new_item_name = forms.CharField(required=False,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Fresh tomatoes, Conference banners'}))
    new_item_unit = forms.ChoiceField(choices=StockItem.UNIT_CHOICES, required=False,
        widget=forms.Select(attrs={'class': FIELD_CLASS}))
    reorder_level = forms.DecimalField(required=False,
        widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Reorder level (optional)'}))

    quantity = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Quantity purchased'}))
    unit_cost = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Price per unit'}))
    total_cost = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Total amount paid'}))
    vat_inclusive = forms.BooleanField(required=False, initial=True)
    purchased_at = forms.DateField(widget=forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}))
    notes = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Notes (optional)'}))

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
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

        quantity = cleaned.get('quantity')
        pricing_mode = cleaned.get('pricing_mode')
        if pricing_mode == 'total':
            total_cost = cleaned.get('total_cost')
            if not total_cost:
                raise forms.ValidationError("Enter the total amount paid.")
            if quantity and quantity > 0:
                cleaned['unit_cost'] = (total_cost / quantity).quantize(Decimal('0.01'))
            else:
                raise forms.ValidationError("Enter a valid quantity to work out the per-unit price.")
        else:
            if not cleaned.get('unit_cost'):
                raise forms.ValidationError("Enter the price per unit.")
        return cleaned