"""Forms for ingredients, recipes, stock movements and preparation batches."""
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import FoodItem, Ingredient, PreparationBatch, Recipe, RecipeIngredient
from .services import units

INPUT = {'class': 'input'}
User = get_user_model()
DEC = dict(max_digits=14, decimal_places=3, min_value=Decimal('0'))


def _num(placeholder='', step='any'):
    return forms.NumberInput(attrs={**INPUT, 'step': step, 'placeholder': placeholder, 'min': '0'})


class IngredientForm(forms.ModelForm):
    """Stock levels and prices are typed in friendly units (kg, l, ...) and stored in the base unit."""
    min_qty = forms.DecimalField(label='Minimum stock level', required=False, widget=_num('e.g. 5'), **DEC)
    min_unit = forms.ChoiceField(label='Minimum unit', choices=units.UNIT_CHOICES, required=False, widget=forms.Select(attrs=INPUT))
    price = forms.DecimalField(label='Purchase cost', required=False, widget=_num('e.g. 40'), max_digits=12, decimal_places=4, min_value=0)
    price_unit = forms.ChoiceField(label='…per', choices=units.UNIT_CHOICES, required=False, widget=forms.Select(attrs=INPUT))
    opening_qty = forms.DecimalField(label='Opening stock', required=False, widget=_num('optional'), **DEC)
    opening_unit = forms.ChoiceField(label='Opening unit', choices=units.UNIT_CHOICES, required=False, widget=forms.Select(attrs=INPUT))

    class Meta:
        model = Ingredient
        fields = ['name', 'category', 'base_unit', 'supplier']
        widgets = {'name': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Potato'}),
                   'category': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Vegetables'}),
                   'base_unit': forms.Select(attrs=INPUT), 'supplier': forms.TextInput(attrs=INPUT)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:                      # the base unit and opening stock are fixed once created
            self.fields['base_unit'].disabled = True
            del self.fields['opening_qty'], self.fields['opening_unit']
            u, ing = self.instance.base_unit, self.instance
            big = {'g': 'kg', 'ml': 'l', 'pcs': 'pcs'}[u]
            self.fields['min_unit'].initial = big
            self.fields['min_qty'].initial = (ing.min_stock / units.UNITS[big][1]).normalize() if ing.min_stock else None
            self.fields['price_unit'].initial = big
            self.fields['price'].initial = (ing.cost_per_unit * units.UNITS[big][1]).normalize() if ing.cost_per_unit else None
        else:
            for f in ('min_unit', 'price_unit', 'opening_unit'):
                self.fields[f].initial = 'kg'

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if Ingredient.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('An ingredient with this name already exists.')
        return name

    def clean(self):
        data = super().clean()
        base = self.instance.base_unit if self.instance.pk else data.get('base_unit')
        for qty, unit, label in (('min_qty', 'min_unit', 'minimum stock'), ('price', 'price_unit', 'cost'),
                                 ('opening_qty', 'opening_unit', 'opening stock')):
            if qty in self.fields and data.get(qty) not in (None, '') and base:
                if not units.compatible(data.get(unit) or base, base):
                    self.add_error(unit, f'The {label} unit does not match this ingredient ({base}).')
        return data

    def save(self, commit=True):
        ing = super().save(commit=False)
        d = self.cleaned_data
        if d.get('min_qty') is not None:
            ing.min_stock = units.to_base(d['min_qty'], d.get('min_unit') or ing.base_unit)
        if d.get('price') is not None:
            # price per kg -> per gram
            ing.cost_per_unit = d['price'] / units.UNITS[d.get('price_unit') or ing.base_unit][1]
        if commit:
            ing.save()
        return ing

    def opening_base(self):
        if self.cleaned_data.get('opening_qty'):
            return units.to_base(self.cleaned_data['opening_qty'], self.cleaned_data.get('opening_unit') or self.cleaned_data['base_unit'])
        return None


class PurchaseForm(forms.Form):
    quantity = forms.DecimalField(widget=_num('Quantity'), max_digits=14, decimal_places=3, min_value=Decimal('0.001'))
    unit = forms.ChoiceField(choices=units.UNIT_CHOICES, widget=forms.Select(attrs=INPUT))
    cost = forms.DecimalField(required=False, widget=_num('Cost per unit (optional)'), max_digits=12, decimal_places=4, min_value=0)
    note = forms.CharField(required=False, max_length=200, widget=forms.TextInput(attrs={**INPUT, 'placeholder': 'Supplier / bill no. (optional)'}))


class AdjustForm(forms.Form):
    new_quantity = forms.DecimalField(label='Counted stock', widget=_num('Counted quantity'), **DEC)
    unit = forms.ChoiceField(choices=units.UNIT_CHOICES, widget=forms.Select(attrs=INPUT))
    reason = forms.CharField(max_length=250, widget=forms.TextInput(attrs={**INPUT, 'placeholder': 'Why? e.g. stock count, spoilage, spillage'}))


class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ['food_item', 'name', 'yield_quantity', 'instructions']
        widgets = {'food_item': forms.Select(attrs=INPUT), 'name': forms.TextInput(attrs=INPUT),
                   'yield_quantity': forms.NumberInput(attrs={**INPUT, 'step': 'any', 'min': '0.01'}),
                   'instructions': forms.Textarea(attrs={**INPUT, 'rows': 4})}
        labels = {'yield_quantity': 'Portions this recipe makes'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = Recipe.objects.exclude(pk=self.instance.pk).values_list('food_item_id', flat=True)
        self.fields['food_item'].queryset = FoodItem.objects.exclude(pk__in=taken).order_by('name')
        if self.instance.pk:
            self.fields['food_item'].disabled = True

    def clean_yield_quantity(self):
        v = self.cleaned_data['yield_quantity']
        if v <= 0:
            raise forms.ValidationError('A recipe must make at least a fraction of a portion.')
        return v


class RecipeLineForm(forms.ModelForm):
    class Meta:
        model = RecipeIngredient
        fields = ['ingredient', 'quantity', 'unit', 'loss_percent']
        widgets = {'ingredient': forms.Select(attrs=INPUT), 'unit': forms.Select(attrs=INPUT),
                   'quantity': forms.NumberInput(attrs={**INPUT, 'step': 'any', 'min': '0'}),
                   'loss_percent': forms.NumberInput(attrs={**INPUT, 'step': 'any', 'min': '0', 'max': '90'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['ingredient'].queryset = Ingredient.objects.filter(is_active=True).order_by('name')
        self.fields['loss_percent'].required = False

    def clean_quantity(self):
        v = self.cleaned_data['quantity']
        if v <= 0:
            raise forms.ValidationError('Must be greater than zero.')
        return v

    def clean_loss_percent(self):
        v = self.cleaned_data.get('loss_percent') or Decimal('0')
        if v < 0 or v > 90:
            raise forms.ValidationError('Use a loss between 0 and 90%.')
        return v


RecipeLineFormSet = forms.inlineformset_factory(Recipe, RecipeIngredient, form=RecipeLineForm, extra=2, can_delete=True)


class BatchCreateForm(forms.ModelForm):
    class Meta:
        model = PreparationBatch
        fields = ['food_item', 'date', 'planned_quantity', 'time_slot', 'assigned_to', 'notes']
        widgets = {'food_item': forms.Select(attrs=INPUT), 'date': forms.DateInput(attrs={**INPUT, 'type': 'date'}),
                   'planned_quantity': forms.NumberInput(attrs={**INPUT, 'min': '1'}),
                   'time_slot': forms.TextInput(attrs={**INPUT, 'placeholder': 'e.g. Morning'}),
                   'assigned_to': forms.Select(attrs=INPUT), 'notes': forms.TextInput(attrs=INPUT)}
        labels = {'assigned_to': 'Assign to', 'planned_quantity': 'Portions to prepare'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['food_item'].queryset = FoodItem.objects.filter(is_active=True, recipe__is_active=True).order_by('name')
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'username')
        self.fields['assigned_to'].label_from_instance = lambda u: f'{u.get_full_name() or u.get_username()}'
        self.fields['assigned_to'].required = True
        self.fields['time_slot'].required = False

    def clean_planned_quantity(self):
        v = self.cleaned_data['planned_quantity']
        if v < 1 or v > 100000:
            raise forms.ValidationError('Enter a whole number of portions from 1 upwards.')
        return v
