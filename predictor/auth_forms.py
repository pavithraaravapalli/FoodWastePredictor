from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import SetPasswordForm, PasswordResetForm
from django.contrib.auth.password_validation import validate_password

from .models import RestaurantSettings, UserProfile

User = get_user_model()
INPUT = {'class': 'input'}


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label='Email or username',
        widget=forms.TextInput(attrs={**INPUT, 'autocomplete': 'username', 'autofocus': True,
                                      'placeholder': 'you@restaurant.com'}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={**INPUT, 'autocomplete': 'current-password',
                                          'placeholder': 'Your password'}))
    remember = forms.BooleanField(required=False, initial=True, label='Keep me signed in')

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        ident, password = data.get('identifier'), data.get('password')
        if ident and password:
            username = ident.strip()
            if '@' in username:  # allow signing in with an email address
                match = User.objects.filter(email__iexact=username).first()
                if match:
                    username = match.get_username()
            self.user = authenticate(self.request, username=username, password=password)
            if self.user is None:
                # Say "waiting for approval" only to someone who proved they know the password.
                candidate = User.objects.filter(username__iexact=username).first()
                if candidate and not candidate.is_active and candidate.check_password(password):
                    if UserProfile.objects.filter(user=candidate, is_pending=True).exists():
                        raise forms.ValidationError('Your account is waiting for the owner to approve it. You will be able to sign in once it is approved.')
                    raise forms.ValidationError('This account has been deactivated. Please contact the restaurant owner.')
                raise forms.ValidationError('That email/username and password do not match.')
        return data


class SignUpForm(forms.Form):
    full_name = forms.CharField(
        max_length=100, label='Full name',
        widget=forms.TextInput(attrs={**INPUT, 'autocomplete': 'name', 'autofocus': True, 'placeholder': 'Asha Verma'}))
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**INPUT, 'autocomplete': 'email', 'placeholder': 'you@restaurant.com'}))
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={**INPUT, 'autocomplete': 'new-password', 'placeholder': 'At least 8 characters'}))
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput(attrs={**INPUT, 'autocomplete': 'new-password', 'placeholder': 'Repeat password'}))

    # Step 2 of the sign-up wizard. There is deliberately NO role field: public sign-up can
    # only ever create a Staff account that the owner must approve.
    phone = forms.CharField(
        max_length=30, required=False, label='Phone',
        widget=forms.TextInput(attrs={**INPUT, 'autocomplete': 'tel', 'placeholder': '+91 98765 43210'}))
    accept_terms = forms.BooleanField(
        label='I agree to the terms of use and privacy policy',
        error_messages={'required': 'Please accept the terms to create your account.'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = RestaurantSettings.load()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists. Try signing in instead.')
        return email

    def clean(self):
        data = super().clean()
        p1, p2 = data.get('password1'), data.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'The two passwords do not match.')
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return data

    def save(self):
        """Creates an INACTIVE Staff account waiting for the owner's approval."""
        d = self.cleaned_data
        first, _, last = d['full_name'].strip().partition(' ')
        user = User.objects.create_user(
            username=d['email'], email=d['email'], password=d['password1'], first_name=first, last_name=last,
            is_active=False)
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': UserProfile.STAFF, 'is_pending': True, 'phone': d['phone'].strip()})
        return user


class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**INPUT, 'autocomplete': 'email', 'autofocus': True, 'placeholder': 'you@restaurant.com'}))


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.update(INPUT)
        self.fields['new_password1'].widget.attrs['placeholder'] = 'At least 8 characters'
        self.fields['new_password2'].widget.attrs['placeholder'] = 'Repeat password'
