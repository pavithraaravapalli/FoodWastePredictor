from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import (
    PasswordResetCompleteView, PasswordResetConfirmView, PasswordResetDoneView, PasswordResetView)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import log_activity
from .auth_forms import LoginForm, SignUpForm, StyledPasswordResetForm, StyledSetPasswordForm


def _next_url(request):
    target = request.POST.get('next') or request.GET.get('next') or ''
    ok = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return target if ok else ''


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm(request, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.user, backend='django.contrib.auth.backends.ModelBackend')
        if not form.cleaned_data['remember']:
            request.session.set_expiry(0)  # end session when the browser closes
        return redirect(_next_url(request) or 'dashboard')
    return render(request, 'predictor/auth/login.html', {'form': form, 'next': _next_url(request)})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        log_activity(None, 'user_create', user, new={'role': 'staff', 'status': 'pending approval'},
                     summary='Self-registered, waiting for owner approval', object_repr=user.get_username())
        messages.success(request, f'Thanks, {user.first_name or "there"}! Your account request has been sent to the owner. '
                                  'You can sign in as soon as it is approved.')
        return redirect('login')
    return render(request, 'predictor/auth/signup.html', {'form': form, 'restaurant': form.restaurant})


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been signed out.')
    return redirect('login')


class ForgotPasswordView(PasswordResetView):
    template_name = 'predictor/auth/forgot_password.html'
    form_class = StyledPasswordResetForm
    email_template_name = 'predictor/auth/password_reset_email.txt'
    subject_template_name = 'predictor/auth/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')

    def form_valid(self, form):
        # django.contrib.sites would otherwise put "example.com" in the email link.
        self.extra_email_context = {'domain': self.request.get_host()}
        return super().form_valid(form)


class ForgotPasswordDoneView(PasswordResetDoneView):
    template_name = 'predictor/auth/forgot_password_done.html'


class ResetPasswordView(PasswordResetConfirmView):
    template_name = 'predictor/auth/reset_password.html'
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy('password_reset_complete')


class ResetPasswordCompleteView(PasswordResetCompleteView):
    template_name = 'predictor/auth/reset_complete.html'
