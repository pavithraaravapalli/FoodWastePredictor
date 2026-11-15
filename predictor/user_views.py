"""Owner-only user management. Every view here requires the `manage_users` permission."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .access import MSG_DENIED, is_owner, log_activity, permission_required, role_of
from .models import UserProfile
from .user_forms import UserForm

User = get_user_model()


def _protected(target, acting):
    """Why `target` can't be deactivated/demoted, or ''."""
    if target.pk == acting.pk:
        return 'You cannot deactivate or change the role of your own account.'
    if target.is_superuser:
        return 'The primary owner (superuser) account is protected.'
    if role_of(target) == UserProfile.OWNER:
        owners = [u for u in User.objects.filter(is_active=True) if role_of(u) == UserProfile.OWNER]
        if len(owners) <= 1:
            return 'At least one active owner must remain.'
    return ''


def _protected_delete(target, acting):
    """Why `target` cannot be deleted, or ''."""
    if target.pk == acting.pk:
        return 'You cannot delete your own account.'
    if target.is_superuser or target.username.lower() in ('admin', 'superuser'):
        return 'The primary owner / system account is protected and cannot be deleted.'
    if role_of(target) == UserProfile.OWNER:
        active_owners = [u for u in User.objects.filter(is_active=True) if role_of(u) == UserProfile.OWNER and u.pk != target.pk]
        if not active_owners:
            return 'Cannot delete the last owner account. At least one active owner must remain.'
    return ''


@permission_required('manage_users')
def user_list(request):
    q = (request.GET.get('q') or '').strip()
    role = request.GET.get('role', '')
    status = request.GET.get('status', '')
    qs = User.objects.select_related('profile').order_by('-is_active', 'first_name', 'username')
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False, profile__is_pending=False)
    elif status == 'pending':
        qs = qs.filter(profile__is_pending=True)
    else:
        status = ''
    rows = list(qs)
    for u in rows:
        u.role_key = role_of(u)
    if role in dict(UserProfile.ROLES):
        rows = [u for u in rows if u.role_key == role]
    else:
        role = ''
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    return render(request, 'predictor/users/list.html', {
        'page': page, 'q': q, 'role': role, 'status': status, 'roles': UserProfile.ROLES,
        'pending_count': UserProfile.objects.filter(is_pending=True).count(),
        'me': request.user, 'can_delete': is_owner(request.user)})


@permission_required('manage_users')
def user_add(request):
    form = UserForm(request.POST or None, acting_user=request.user)
    if request.method == 'POST':
        if form.is_valid():
            user = form.save()
            role = role_of(user)
            log_activity(request.user, 'user_create', user, new={'role': role, 'email': user.email},
                         object_repr=user.get_username())
            if form.cleaned_data.get('password'):
                messages.success(request, f'Created {user.get_username()} as {role}. They can sign in with the temporary password you set.')
            else:
                sent = _send_reset(request, user)
                messages.success(request, f'Created {user.get_username()} as {role}. ' +
                                 ('A password link has been emailed to them.' if sent else 'Use "Send password reset" to let them set a password.'))
            return redirect('user_list')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return render(request, 'predictor/users/form.html', {'form': form, 'target': None})


@permission_required('manage_users')
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    form = UserForm(request.POST or None, instance=target, acting_user=request.user)
    if request.method == 'POST':
        old_role = role_of(target)
        old_email = target.email
        if form.is_valid():
            user = form.save()
            new_role = role_of(user)
            if new_role != old_role:
                log_activity(request.user, 'user_role', user, old={'role': old_role}, new={'role': new_role},
                             object_repr=user.get_username())
            else:
                log_activity(request.user, 'user_update', user,
                             old={'email': old_email} if old_email != user.email else None,
                             new={'email': user.email} if old_email != user.email else None,
                             object_repr=user.get_username(), summary='Profile/permissions updated')
            messages.success(request, f'Saved changes to {user.get_username()}.')
            return redirect('user_list')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return render(request, 'predictor/users/form.html', {'form': form, 'target': target})


@permission_required('manage_users')
@require_POST
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target.is_active:
        why = _protected(target, request.user)
        if why:
            messages.error(request, why)
            return redirect('user_list')
        target.is_active = False
        kind, text = 'user_deactivate', f'{target.get_username()} has been deactivated and can no longer sign in. Their records are kept.'
    else:
        target.is_active = True
        kind, text = 'user_reactivate', f'{target.get_username()} has been reactivated.'
    target.save(update_fields=['is_active'])
    UserProfile.objects.filter(user=target).update(is_pending=False)
    log_activity(request.user, kind, target, object_repr=target.get_username())
    messages.success(request, text)
    return redirect('user_list')


@permission_required('manage_users')
@require_POST
def user_delete(request, pk):
    """Permanently delete a deactivated account. Owner-only."""
    if not is_owner(request.user):
        raise PermissionDenied(MSG_DENIED)
    target = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    if target.is_active:
        messages.error(request, 'Active accounts cannot be deleted directly. Please deactivate the account first.')
        return redirect('user_list')
    why = _protected_delete(target, request.user)
    if why:
        messages.error(request, why)
        return redirect('user_list')

    username = target.get_username()
    display = target.get_full_name() or username

    log_activity(
        request.user,
        'user_deactivate',
        target,
        summary='Account permanently deleted. Historical business records and activity logs preserved.',
        object_repr=username,
    )

    target.delete()

    messages.success(
        request,
        f'Account "{display}" ({username}) has been permanently deleted. Historical business records and activity logs have been preserved.',
    )
    return redirect('user_list')


@permission_required('manage_users')
@require_POST
def user_approve(request, pk):
    """Approve (activate) or decline a self-registered sign-up."""
    target = get_object_or_404(User, pk=pk, profile__is_pending=True)
    if request.POST.get('decision') == 'decline':
        log_activity(request.user, 'user_deactivate', target, summary='Sign-up declined', object_repr=target.get_username())
        name = target.get_username()
        target.delete()          # never active, owns no records, so deleting is safe
        messages.success(request, f'Declined the sign-up from {name}.')
    else:
        target.is_active = True
        target.save(update_fields=['is_active'])
        UserProfile.objects.filter(user=target).update(is_pending=False)
        log_activity(request.user, 'user_approve', target, new={'role': role_of(target)}, object_repr=target.get_username())
        messages.success(request, f'Approved {target.get_username()} as {role_of(target)}. Change the role if they need more access.')
    return redirect('user_list')


def _send_reset(request, user):
    """Emails Django's one-time, expiring reset link. No password or token is ever shown or logged."""
    if not user.email or not user.is_active:
        return False
    form = PasswordResetForm({'email': user.email})
    if not form.is_valid():
        return False
    form.save(request=request, use_https=request.is_secure(),
              email_template_name='predictor/auth/password_reset_email.txt',
              subject_template_name='predictor/auth/password_reset_subject.txt',
              extra_email_context={'domain': request.get_host()})
    return True


@permission_required('manage_users')
@require_POST
def user_send_reset(request, pk):
    target = get_object_or_404(User, pk=pk)
    if _send_reset(request, target):
        log_activity(request.user, 'user_reset', target, object_repr=target.get_username(), summary='Reset email sent')
        messages.success(request, f'A password reset link was emailed to {target.email}. It expires automatically.')
    else:
        messages.error(request, 'That account has no email address or is deactivated, so no reset link was sent.')
    return redirect('user_list')
