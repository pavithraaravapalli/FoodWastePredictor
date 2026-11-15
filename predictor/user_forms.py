from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password

from .access import EXTRA_GRANTS
from .models import UserProfile

User = get_user_model()
INPUT = {'class': 'input'}


class UserForm(forms.Form):
    """Owner-only form to create or edit a team member. Passwords are always hashed by Django."""

    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs=INPUT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=INPUT))
    email = forms.EmailField(widget=forms.EmailInput(attrs=INPUT))
    phone = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=INPUT))
    role = forms.ChoiceField(choices=UserProfile.ROLES, widget=forms.Select(attrs=INPUT))
    manage_items = forms.BooleanField(required=False, label='Can add and edit food items')
    view_activity = forms.BooleanField(required=False, label='Can view the activity log')
    enter_conditions = forms.BooleanField(required=False, label='Can enter weather, traffic and event information')
    password = forms.CharField(
        required=False, label='Temporary password', widget=forms.PasswordInput(attrs={**INPUT, 'autocomplete': 'new-password'}),
        help_text='Optional. Leave empty to email the person a link to choose their own password.')

    def __init__(self, *args, instance=None, acting_user=None, **kwargs):
        self.instance = instance
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.editing_self = bool(instance and acting_user and instance.pk == acting_user.pk)
        self.protected = bool(instance and instance.is_superuser)
        if instance is not None:
            del self.fields['password']
            self.fields['role'].disabled = self.editing_self or self.protected
            perms = set(instance.user_permissions.values_list('codename', flat=True))
            if not args or not args[0]:
                self.initial.update({
                    'first_name': instance.first_name, 'last_name': instance.last_name, 'email': instance.email,
                    'phone': getattr(getattr(instance, 'profile', None), 'phone', ''),
                    'role': getattr(getattr(instance, 'profile', None), 'role', UserProfile.STAFF) if not instance.is_superuser else UserProfile.OWNER,
                    **{key: all(c in perms for c in codes) for key, (_, codes) in EXTRA_GRANTS.items()},
                })

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        clash = User.objects.filter(email__iexact=email) | User.objects.filter(username__iexact=email)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email

    def clean_password(self):
        pw = self.cleaned_data.get('password') or ''
        if pw:
            validate_password(pw)
        return pw

    def save(self):
        d = self.cleaned_data
        if self.instance is None:
            user = User(username=d['email'], email=d['email'], is_staff=False, is_superuser=False)
            if d.get('password'):
                user.set_password(d['password'])
            else:
                # A random, never-displayed password: the person sets their own through the
                # emailed reset link. (Django only emails reset links to usable passwords.)
                from django.utils.crypto import get_random_string
                user.set_password(get_random_string(40))
        else:
            user = self.instance
            user.email = d['email']
        user.first_name, user.last_name = d['first_name'].strip(), d['last_name'].strip()
        user.save()
        profile = UserProfile.for_user(user)
        profile.phone = d['phone'].strip()
        if not (self.editing_self or self.protected):
            profile.role = d['role']
        profile.save()
        # Optional per-user grants on top of the role (never for owners, who already have everything)
        wanted = set()
        if profile.role != UserProfile.OWNER:
            for key, (_, codes) in EXTRA_GRANTS.items():
                if d.get(key):
                    wanted.update(codes)
        all_extra = {c for _, codes in EXTRA_GRANTS.values() for c in codes}
        keep = set(user.user_permissions.exclude(codename__in=all_extra).values_list('codename', flat=True))
        perms = Permission.objects.filter(content_type__app_label='predictor', codename__in=wanted | keep)
        user.user_permissions.set(perms)
        return user
