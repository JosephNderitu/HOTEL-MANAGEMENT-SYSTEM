from django import forms
from .models import GateLog
from public_site.models import *
from .models import Payment

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


class ManualBookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['booking_type', 'source', 'guest_name', 'guest_phone', 'guest_id_no',
                  'room_type', 'conference_room', 'check_in', 'check_out', 'amount', 'status']
        widgets = {
            'booking_type': forms.Select(attrs={'class': FIELD_CLASS}),
            'source': forms.Select(attrs={'class': FIELD_CLASS}),
            'guest_name': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Guest full name'}),
            'guest_phone': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Phone number'}),
            'guest_id_no': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'National ID / Passport'}),
            'room_type': forms.Select(attrs={'class': FIELD_CLASS}),
            'conference_room': forms.Select(attrs={'class': FIELD_CLASS}),
            'check_in': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}),
            'check_out': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}),
            'amount': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
            'status': forms.Select(attrs={'class': FIELD_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source'].choices = [('phone', 'Phone'), ('walkin', 'Walk-in')]
        self.fields['room_type'].queryset = RoomType.objects.filter(is_active=True)
        self.fields['room_type'].required = False
        self.fields['room_type'].label_from_instance = lambda rt: f"{rt.name} (KSh {rt.price_min:,.0f}–{rt.price_max:,.0f}/night)"
        self.fields['conference_room'].queryset = ConferenceRoom.objects.filter(is_active=True)
        self.fields['conference_room'].required = False
        self.fields['conference_room'].label_from_instance = lambda cr: f"{cr.name} — {cr.get_tier_display()} (KSh {cr.price_min:,.0f}–{cr.price_max:,.0f})"

    def clean(self):
        cleaned = super().clean()
        booking_type = cleaned.get('booking_type')
        if booking_type == 'room' and not cleaned.get('room_type'):
            raise forms.ValidationError("Select a room type for a room booking.")
        if booking_type == 'conference' and not cleaned.get('conference_room'):
            raise forms.ValidationError("Select a conference room for a conference booking.")
        return cleaned


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'amount_tendered', 'method', 'reference']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'id': 'id_amount'}),
            'amount_tendered': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Cash given (optional)', 'id': 'id_amount_tendered'}),
            'method': forms.Select(attrs={'class': FIELD_CLASS}),
            'reference': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'M-Pesa code / reference (optional)'}),
        }


class TableRangeForm(forms.Form):
    start = forms.IntegerField()
    end = forms.IntegerField()
    capacity = forms.IntegerField(initial=4)
    
class SettlementForm(forms.Form):
    amount = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'id': 'id_amount'}))
    amount_tendered = forms.DecimalField(required=False, widget=forms.NumberInput(
        attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Cash given (optional)', 'id': 'id_amount_tendered'}))
    method = forms.ChoiceField(choices=Payment.METHOD_CHOICES, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    reference = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': FIELD_CLASS, 'placeholder': 'M-Pesa code / reference (optional)'}))
    
class RoomCheckinForm(forms.Form):
    guest_name = forms.CharField(widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Guest full name'}))
    guest_phone = forms.CharField(widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Phone number'}))
    guest_id_no = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'National ID / Passport'}))
    check_out = forms.DateField(widget=forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}))
    amount = forms.DecimalField(widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01', 'placeholder': 'Rate per night'}))