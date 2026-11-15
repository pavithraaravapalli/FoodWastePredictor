from django import forms
from django.utils import timezone
from .models import FoodItem, FoodRecord


class FoodRecordForm(forms.ModelForm):

    class Meta:
        model = FoodRecord

        fields = [
            'food_item',
            'date',
            'prepared_quantity',
            'sold_quantity',
            'weather',
            'is_holiday',
            'event_name',
        ]

        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date',
                    'min': '2000-01-01',
                    'placeholder': 'DD-MM-YYYY',
                }
            ),

            'prepared_quantity': forms.NumberInput(
                attrs={
                    'min': 0
                }
            ),

            'sold_quantity': forms.NumberInput(
                attrs={
                    'min': 0
                }
            ),

            'event_name': forms.TextInput(
                attrs={
                    'placeholder': 'Optional'
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'is_holiday':
                field.widget.attrs['class'] = 'checkbox'
            else:
                field.widget.attrs['class'] = 'input'
        self.fields['weather'].widget.attrs.update(
            {'list': 'weather-options', 'placeholder': 'e.g. Sunny, Rainy'})
        self.fields['food_item'].empty_label = 'Select a food item'
        # New records can only use active items; keep the current one when editing.
        active = FoodItem.objects.filter(is_active=True)
        if self.instance.pk:
            active = FoodItem.objects.filter(is_active=True) | FoodItem.objects.filter(pk=self.instance.food_item_id)
        self.fields['food_item'].queryset = active.distinct()
        self.fields['is_holiday'].label = 'Public holiday'

    def clean_date(self):
        date = self.cleaned_data['date']
        if date.year < 2000:
            raise forms.ValidationError('Enter a four-digit year from 2000 onwards (DD-MM-YYYY).')
        if date > timezone.localdate():
            raise forms.ValidationError('Records cannot be dated in the future.')
        return date

    def clean(self):

        cleaned_data = super().clean()

        prepared = cleaned_data.get(
            'prepared_quantity'
        )

        sold = cleaned_data.get(
            'sold_quantity'
        )

        if (
            prepared is not None
            and sold is not None
            and sold > prepared
        ):
            raise forms.ValidationError(
                'Sold quantity cannot be greater than prepared quantity.'
            )

        item, date = cleaned_data.get('food_item'), cleaned_data.get('date')
        if item and date:
            duplicate = FoodRecord.objects.filter(food_item=item, date=date)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(
                    f'A record for {item.name} on {date:%d-%m-%Y} already exists. '
                    'Edit that record instead of adding a second one.')

        return cleaned_data


class FoodItemForm(forms.ModelForm):
    class Meta:
        model = FoodItem
        fields = ['name', 'category', 'cost_per_portion']
        labels = {'cost_per_portion': 'Cost per portion'}
        widgets = {
            'name': forms.TextInput(attrs={'class': 'input', 'placeholder': 'e.g. Masala Dosa', 'autofocus': True}),
            'category': forms.TextInput(attrs={'class': 'input', 'list': 'category-options', 'placeholder': 'e.g. Breakfast'}),
            'cost_per_portion': forms.NumberInput(attrs={'class': 'input', 'min': '0', 'step': '0.01', 'placeholder': '0.00'}),
        }

    def __init__(self, *args, can_set_price=True, **kwargs):
        super().__init__(*args, **kwargs)
        # Without the price permission the field is not accepted at all, so a forged
        # POST containing cost_per_portion is ignored on the server.
        self.can_set_price = can_set_price
        if not can_set_price:
            del self.fields['cost_per_portion']

    def save(self, commit=True):
        item = super().save(commit=False)
        if not self.can_set_price and item.cost_per_portion is None:
            item.cost_per_portion = 0
        if commit:
            item.save()
        return item

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        clash = FoodItem.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A food item with this name already exists.')
        return name

    def clean_category(self):
        return ' '.join(self.cleaned_data['category'].split())

    def clean_cost_per_portion(self):
        cost = self.cleaned_data['cost_per_portion']
        if cost < 0:
            raise forms.ValidationError('Cost cannot be negative.')
        return cost


# --- Settings / profile -------------------------------------------------------
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm

from .models import RestaurantSettings, UserProfile


class ProfileForm(forms.Form):
    first_name = forms.CharField(max_length=150, label='First name', widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'given-name'}))
    last_name = forms.CharField(max_length=150, required=False, label='Last name', widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'family-name'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'input', 'autocomplete': 'email'}))
    phone = forms.CharField(max_length=30, required=False, label='Phone', widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'tel', 'placeholder': '+91 98765 43210'}))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        digits = sum(c.isdigit() for c in phone)
        if phone and (digits < 7 or not all(c.isdigit() or c in '+-() .' for c in phone)):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    def save(self):
        d = self.cleaned_data
        u = self.user
        u.first_name, u.last_name, u.email = d['first_name'].strip(), d['last_name'].strip(), d['email']
        u.save(update_fields=['first_name', 'last_name', 'email'])
        # NOTE: the role is deliberately NOT editable here - nobody can change their own role.
        UserProfile.objects.update_or_create(user=u, defaults={'phone': d['phone']})
        return u


class RestaurantForm(forms.ModelForm):
    class Meta:
        model = RestaurantSettings
        fields = ['name', 'business_type', 'city', 'address', 'phone', 'latitude', 'longitude',
                  'currency_symbol', 'waste_alert_threshold']
        labels = {'name': 'Restaurant name', 'business_type': 'Type of business', 'city': 'City or highway',
                  'address': 'Address or landmark', 'phone': 'Restaurant phone',
                  'currency_symbol': 'Currency symbol', 'waste_alert_threshold': 'Waste alert threshold (%)'}
        widgets = {
            'name': forms.TextInput(attrs={'class': 'input'}),
            'business_type': forms.Select(attrs={'class': 'input'}),
            'city': forms.TextInput(attrs={'class': 'input'}),
            'address': forms.TextInput(attrs={'class': 'input', 'placeholder': 'e.g. NH48, Km 112, near Vapi toll'}),
            'phone': forms.TextInput(attrs={'class': 'input'}),
            'latitude': forms.NumberInput(attrs={'class': 'input', 'step': 'any', 'placeholder': '19.0760'}),
            'longitude': forms.NumberInput(attrs={'class': 'input', 'step': 'any', 'placeholder': '72.8777'}),
            'currency_symbol': forms.TextInput(attrs={'class': 'input', 'maxlength': 4}),
            'waste_alert_threshold': forms.NumberInput(attrs={'class': 'input', 'min': 1, 'max': 100}),
        }

    def clean_latitude(self):
        v = self.cleaned_data.get('latitude')
        if v is not None and not -90 <= v <= 90:
            raise forms.ValidationError('Latitude must be between -90 and 90.')
        return v

    def clean_longitude(self):
        v = self.cleaned_data.get('longitude')
        if v is not None and not -180 <= v <= 180:
            raise forms.ValidationError('Longitude must be between -180 and 180.')
        return v

    def clean_waste_alert_threshold(self):
        v = self.cleaned_data['waste_alert_threshold']
        if not 1 <= v <= 100:
            raise forms.ValidationError('Choose a percentage between 1 and 100.')
        return v

    def clean(self):
        data = super().clean()
        if (data.get('latitude') is None) != (data.get('longitude') is None):
            raise forms.ValidationError('Enter both latitude and longitude, or leave both empty.')
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.is_configured = True
        if commit:
            obj.save()
        return obj


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'input'
        self.fields['old_password'].widget.attrs.pop('autofocus', None)


# --- Traffic ------------------------------------------------------------------
from datetime import timedelta

from django.utils import timezone as _tz

from .models import TrafficObservation


class TrafficForm(forms.ModelForm):
    observed_at = forms.DateTimeField(
        label='Observed at', input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(attrs={'class': 'input', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))

    class Meta:
        model = TrafficObservation
        fields = ['condition', 'observed_at', 'notes']
        widgets = {
            'condition': forms.RadioSelect,
            'notes': forms.TextInput(attrs={'class': 'input', 'placeholder': 'e.g. Queue at toll plaza, accident ahead'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only the four real conditions: no blank "select an option" radio.
        self.fields['condition'].choices = TrafficObservation.CONDITIONS

    def clean_observed_at(self):
        when = self.cleaned_data['observed_at']
        if when > _tz.now() + timedelta(minutes=5):
            raise forms.ValidationError('Traffic can only be recorded for now or the past.')
        return when
