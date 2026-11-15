"""Forms for manual weather and local-event entry (the fallback to the automatic modes)."""
from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import LocalEvent, WeatherObservation

INPUT = {'class': 'input'}
DT_FORMATS = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M']


class WeatherForm(forms.ModelForm):
    target_date = forms.DateField(label='Day', widget=forms.DateInput(attrs={**INPUT, 'type': 'date'}))
    temperature_c = forms.DecimalField(label='Temperature (°C)', required=False, min_value=-30, max_value=60, decimal_places=1,
                                       widget=forms.NumberInput(attrs={**INPUT, 'step': '0.1', 'placeholder': 'e.g. 31'}))

    class Meta:
        model = WeatherObservation
        fields = ['target_date', 'condition', 'temperature_c', 'notes']
        widgets = {'condition': forms.RadioSelect,
                   'notes': forms.TextInput(attrs={**INPUT, 'placeholder': 'Optional, e.g. heavy rain after 4 pm'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['condition'].choices = [c for c in WeatherObservation.CONDITIONS if c[0] != 'other']
        self.fields['condition'].initial = 'sunny'

    def clean_target_date(self):
        day, today = self.cleaned_data['target_date'], timezone.localdate()
        if day < today - timedelta(days=30) or day > today + timedelta(days=14):
            raise forms.ValidationError('Choose a day from the last 30 days up to 14 days ahead.')
        return day


class LocalEventForm(forms.ModelForm):
    start_at = forms.DateTimeField(label='Starts', input_formats=DT_FORMATS,
                                   widget=forms.DateTimeInput(attrs={**INPUT, 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    end_at = forms.DateTimeField(label='Ends', required=False, input_formats=DT_FORMATS,
                                 widget=forms.DateTimeInput(attrs={**INPUT, 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))

    class Meta:
        model = LocalEvent
        fields = ['name', 'category', 'venue', 'address', 'start_at', 'end_at', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Ganesh Chaturthi procession'}),
            'category': forms.Select(attrs=INPUT),
            'venue': forms.TextInput(attrs={**INPUT, 'placeholder': 'Optional'}),
            'address': forms.TextInput(attrs={**INPUT, 'placeholder': 'Optional'}),
            'notes': forms.TextInput(attrs={**INPUT, 'placeholder': 'Optional, e.g. expect 2,000 visitors'}),
        }

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean(self):
        data = super().clean()
        start, end = data.get('start_at'), data.get('end_at')
        if start and end and end < start:
            self.add_error('end_at', 'The event cannot end before it starts.')
        if start and not self.errors:
            clash = LocalEvent.objects.filter(source='manual', name__iexact=data['name'], start_at__date=timezone.localtime(start).date())
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError('This event is already in your list for that day.')   # duplicate prevention
        return data
