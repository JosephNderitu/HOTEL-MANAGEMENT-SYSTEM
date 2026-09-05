from django import forms
from .models import GateLog

FIELD_CLASS = 'w-full border border-[#0B6B3A] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#0B6B3A]/20'


class GateEntryForm(forms.ModelForm):
    class Meta:
        model = GateLog
        fields = ['guest_name', 'phone_number', 'vehicle_make', 'plate_number', 'purpose', 'parking_spot', 'notes']
        widgets = {
            'guest_name': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Guest or visitor name'}),
            'phone_number': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Phone number (optional)'}),
            'vehicle_make': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Toyota Probox'}),
            'plate_number': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. KDA 123B'}),
            'purpose': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Visiting guest, Delivery'}),
            'parking_spot': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. P3 (optional)'}),
            'notes': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2, 'placeholder': 'Any additional notes (optional)'}),
        }
        
class GateEditForm(forms.ModelForm):
    class Meta:
        model = GateLog
        fields = ['guest_name', 'phone_number', 'vehicle_make', 'plate_number', 'purpose', 'parking_spot', 'notes']
        widgets = {
            'guest_name': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'phone_number': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'vehicle_make': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'plate_number': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'purpose': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'parking_spot': forms.TextInput(attrs={'class': FIELD_CLASS}),
            'notes': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2}),
        }