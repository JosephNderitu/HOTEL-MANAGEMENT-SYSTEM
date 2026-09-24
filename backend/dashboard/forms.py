from django import forms
from .models import GateLog
from public_site.models import *
from .models import *
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from hrm.models import *
from .permissions import has_full_access, FULL_ACCESS_GROUPS

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
    
##############################################
######### ---------HRM-----------#############
    
class LeaveRequestForm(forms.ModelForm):
    days_requested = forms.IntegerField(
        min_value=1, widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'id': 'id_days_requested'})
    )

    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'reason']
        widgets = {
            'leave_type': forms.Select(attrs={'class': FIELD_CLASS, 'id': 'id_leave_type'}),
            'start_date': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date', 'id': 'id_start_date'}),
            'reason': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2}),
        }

    def __init__(self, *args, staff=None, **kwargs):
        self.staff = staff
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        leave_type = cleaned.get('leave_type')
        days = cleaned.get('days_requested')
        start = cleaned.get('start_date')
        if leave_type and days and start and self.staff:
            balance, _ = LeaveBalance.objects.get_or_create(
                staff=self.staff, leave_type=leave_type, year=start.year,
                defaults={'allocated_days': leave_type.default_days_per_year},
            )
            if days > balance.remaining_days:
                raise forms.ValidationError(
                    f"You only have {balance.remaining_days} day(s) of {leave_type} remaining."
                )
            cleaned['end_date'] = start + timedelta(days=days - 1)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.staff = self.staff
        instance.end_date = self.cleaned_data['end_date']
        if commit:
            instance.save()
        return instance


class ShiftAssignmentForm(forms.ModelForm):
    class Meta:
        model = ShiftAssignment
        fields = ['staff', 'shift', 'date']
        widgets = {
            'staff': forms.Select(attrs={'class': FIELD_CLASS}),
            'shift': forms.Select(attrs={'class': FIELD_CLASS}),
            'date': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['staff'].queryset = User.objects.filter(is_staff=True).order_by('first_name')


class PayrollForm(forms.ModelForm):
    class Meta:
        model = PayrollRecord
        fields = ['period_month', 'period_year', 'basic_salary', 'allowances_total', 'allowances_notes', 'deductions_total', 'deductions_notes', 'overtime_hours', 'overtime_rate']
        widgets = {
            'period_month': forms.NumberInput(attrs={'class': FIELD_CLASS, 'min': 1, 'max': 12}),
            'period_year': forms.NumberInput(attrs={'class': FIELD_CLASS}),
            'basic_salary': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
            'allowances_total': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
            'allowances_notes': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Housing, transport'}),
            'deductions_total': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
            'deductions_notes': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. NHIF, NSSF, PAYE'}),
            'overtime_hours': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
            'overtime_rate': forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}),
        }


class PerformanceReviewForm(forms.ModelForm):
    class Meta:
        model = PerformanceReview
        fields = ['review_period', 'overall_rating', 'strengths', 'areas_to_improve', 'goals']
        widgets = {
            'review_period': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. Q1 2026'}),
            'overall_rating': forms.Select(choices=[(i, i) for i in range(1, 6)], attrs={'class': FIELD_CLASS}),
            'strengths': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 3}),
            'areas_to_improve': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 3}),
            'goals': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2}),
        }
        
class BulkShiftAssignForm(forms.Form):
    staff = forms.ModelChoiceField(queryset=User.objects.filter(is_staff=True).order_by('first_name'), widget=forms.Select(attrs={'class': FIELD_CLASS}))
    shift = forms.ModelChoiceField(queryset=Shift.objects.all(), widget=forms.Select(attrs={'class': FIELD_CLASS}))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            raise forms.ValidationError("End date can't be before start date.")
        return cleaned


class StaffWeeklyOffForm(forms.Form):
    staff = forms.ModelChoiceField(queryset=User.objects.filter(is_staff=True).order_by('first_name'), widget=forms.Select(attrs={'class': FIELD_CLASS}))
    weekdays = forms.MultipleChoiceField(choices=StaffWeeklyOff.WEEKDAY_CHOICES, widget=forms.CheckboxSelectMultiple, required=False)


class ShiftSwapRequestForm(forms.ModelForm):
    class Meta:
        model = ShiftSwapRequest
        fields = ['mode', 'start_date', 'end_date', 'requested_shift', 'trade_with', 'reason']
        widgets = {
            'mode': forms.RadioSelect,
            'start_date': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}),
            'requested_shift': forms.Select(attrs={'class': FIELD_CLASS}),
            'trade_with': forms.Select(attrs={'class': FIELD_CLASS}),
            'reason': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2, 'placeholder': 'Why do you need this change?'}),
        }

    def __init__(self, *args, staff=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(is_staff=True).order_by('first_name')
        if staff:
            qs = qs.exclude(id=staff.id)
            my_profile = getattr(staff, 'staff_profile', None)
            if my_profile and my_profile.position:
                qs = qs.filter(staff_profile__position__iexact=my_profile.position)
        self.fields['trade_with'].queryset = qs
        self.fields['requested_shift'].required = False
        self.fields['trade_with'].required = False

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')
        if mode == 'self' and not cleaned.get('requested_shift'):
            self.add_error('requested_shift', "Pick the shift you want to move to.")
        if mode == 'trade' and not cleaned.get('trade_with'):
            self.add_error('trade_with', "Pick the colleague you want to trade with.")
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            raise forms.ValidationError("End date can't be before start date.")
        return cleaned

def _normalize_id(s):
    return ''.join(ch for ch in (s or '') if ch.isalnum()).lower()

FIELD_CLASS = (
    "w-full bg-white text-slate-900 placeholder-slate-400 border border-slate-300 "
    "focus:border-[#C9A227] focus:ring-2 focus:ring-[#C9A227]/30 rounded-xl px-4 py-3 "
    "text-sm focus:outline-none transition-all caret-slate-900"
)

class ClockVerifyForm(forms.Form):
    national_id = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': FIELD_CLASS, 
            'placeholder': 'Your National ID / Passport No.', 
            'autocomplete': 'off'
        })
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': FIELD_CLASS, 
            'placeholder': 'Your phone number', 
            'autocomplete': 'off'
        })
    )
    lat = forms.FloatField(widget=forms.HiddenInput())
    lng = forms.FloatField(widget=forms.HiddenInput())

    def __init__(self, *args, staff=None, **kwargs):
        self.staff = staff
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        national_id = cleaned.get('national_id', '')
        phone = cleaned.get('phone', '')
        profile = getattr(self.staff, 'staff_profile', None)
        if not profile or _normalize_id(national_id) != _normalize_id(profile.national_id) \
                or _normalize_id(phone) != _normalize_id(profile.phone):
            raise forms.ValidationError("Your ID number or phone number doesn't match our records. Please check and try again.")
        return cleaned
    
##################################################################
########## HRM MANAGEMENT #####################
class StaffHireForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Login username'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': FIELD_CLASS}))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={'class': FIELD_CLASS}), label='Set Password')
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={'class': FIELD_CLASS}), label='Confirm Password')
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(), widget=forms.CheckboxSelectMultiple, required=True, label='Role(s)'
    )
    national_id = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    position = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    department = forms.ChoiceField(choices=StaffProfile.DEPARTMENT_CHOICES, required=False, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    department_note = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    date_joined_role = forms.DateField(widget=forms.DateInput(attrs={'class': FIELD_CLASS, 'type': 'date'}))
    pay_type = forms.ChoiceField(choices=StaffProfile.PAY_TYPE_CHOICES, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    pay_rate = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}))
    emergency_contact_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    emergency_contact_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))

    def __init__(self, *args, requesting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Group.objects.all().order_by('name')
        if not has_full_access(requesting_user):
            qs = qs.exclude(name__in=FULL_ACCESS_GROUPS)
        self.fields['groups'].queryset = qs

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_national_id(self):
        nid = self.cleaned_data['national_id'].strip()
        if StaffProfile.objects.filter(national_id__iexact=nid).exists():
            raise forms.ValidationError("A staff member with this National ID already exists.")
        return nid

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords don't match.")
        elif p1:
            try:
                validate_password(p1)
            except DjangoValidationError as e:
                self.add_error('password1', e)
        return cleaned

    def save(self, created_by):
        data = self.cleaned_data
        user = User.objects.create_user(
            username=data['username'], email=data.get('email', ''),
            first_name=data['first_name'], last_name=data['last_name'],
            password=data['password1'], is_staff=True, is_active=True,
        )
        user.groups.set(data['groups'])
        profile = StaffProfile.objects.create(
            user=user, national_id=data['national_id'], phone=data['phone'],
            position=data.get('position', ''), department=data.get('department', ''),
            department_note=data.get('department_note', ''), date_joined_role=data['date_joined_role'],
            pay_type=data['pay_type'], pay_rate=data['pay_rate'],
            emergency_contact_name=data.get('emergency_contact_name', ''),
            emergency_contact_phone=data.get('emergency_contact_phone', ''),
        )
        EmploymentAction.objects.create(
            staff=user, action='hired', new_position=profile.position, new_department=profile.department,
            note=f"Hired as {profile.position or 'staff'}", performed_by=created_by,
        )
        return user, profile

class StaffRoleUpdateForm(forms.Form):
    position = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    department = forms.ChoiceField(choices=StaffProfile.DEPARTMENT_CHOICES, required=False, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    department_note = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': FIELD_CLASS}))
    pay_type = forms.ChoiceField(choices=StaffProfile.PAY_TYPE_CHOICES, widget=forms.Select(attrs={'class': FIELD_CLASS}))
    pay_rate = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': '0.01'}))
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.none(), widget=forms.CheckboxSelectMultiple, required=True, label='Role(s)')
    note = forms.CharField(widget=forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 2, 'placeholder': 'Reason for this change (kept in their history)'}))

    def __init__(self, *args, requesting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Group.objects.all().order_by('name')
        if not has_full_access(requesting_user):
            qs = qs.exclude(name__in=FULL_ACCESS_GROUPS)
        self.fields['groups'].queryset = qs
        
