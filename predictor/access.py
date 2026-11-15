"""Role-based access control: roles, groups, permission checks and the activity log.

Every sensitive view is protected on the SERVER with `permission_required`. Hiding a
menu item in a template is only a convenience - it is never the security boundary.

Roles (stored on UserProfile.role, mirrored into a Django Group of the same name):
  Owner   - everything, including users, roles, activity log, restaurant settings.
  Manager - day-to-day operations: items, records, approvals, forecasts, plans, reports.
  Staff   - add and submit their own daily records, view items and approved plans.
"""
import functools
import re
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils import timezone

from .models import ActivityLog, UserProfile

OWNER, MANAGER, STAFF = UserProfile.OWNER, UserProfile.MANAGER, UserProfile.STAFF
GROUP_NAMES = {OWNER: 'Owner', MANAGER: 'Manager', STAFF: 'Staff'}

MSG_DENIED = 'You do not have permission to perform this action.'
MSG_LOCKED = 'This record has already been approved and is locked.'
MSG_WAITING = 'Waiting for manager approval.'

_FOODITEM = ['view_fooditem', 'add_fooditem', 'change_fooditem', 'delete_fooditem', 'change_item_price']
_RECORD = ['view_foodrecord', 'add_foodrecord', 'change_foodrecord', 'delete_foodrecord', 'approve_foodrecord']
_PLAN = ['view_prediction', 'add_prediction', 'change_prediction', 'generate_predictions', 'manage_plans']
_KITCHEN = ['view_ingredient', 'add_ingredient', 'change_ingredient', 'delete_ingredient',
            'view_recipe', 'add_recipe', 'change_recipe', 'delete_recipe',
            'view_recipeingredient', 'add_recipeingredient', 'change_recipeingredient', 'delete_recipeingredient',
            'view_stocktransaction', 'manage_stock',
            'view_preparationbatch', 'add_preparationbatch', 'change_preparationbatch',
            'record_consumption', 'review_consumption', 'view_ingredientusage']
_TRAFFIC = ['view_trafficobservation', 'add_trafficobservation', 'delete_trafficobservation']
_REPORTS = ['view_analytics', 'view_reports']
_WEATHER = ['view_weatherobservation', 'add_weatherobservation']
_EVENTS = ['view_localevent', 'add_localevent', 'change_localevent', 'delete_localevent']
_SOURCES = ['manage_data_sources']

# Permissions that can be granted to ONE user on top of their role (Settings > Users).
EXTRA_GRANTS = {
    'manage_items': ('Manage food items', ['add_fooditem', 'change_fooditem']),
    'view_activity': ('View the activity log', ['view_activitylog']),
    'enter_conditions': ('Enter weather, traffic and event information',
                         ['view_weatherobservation', 'add_weatherobservation', 'view_trafficobservation',
                          'add_trafficobservation', 'view_localevent', 'add_localevent']),
}

ROLE_PERMISSIONS = {
    # Owner: every permission of this app (resolved at sync time).
    OWNER: None,
    MANAGER: _FOODITEM + _RECORD + _PLAN + _TRAFFIC + _REPORTS + _WEATHER + _EVENTS + _SOURCES + _KITCHEN,
    STAFF: ['view_fooditem', 'view_foodrecord', 'add_foodrecord', 'view_prediction',
            # recipes are for cooking: staff may read them (no costs) and record actual usage on their own batches
            'view_recipe', 'view_recipeingredient', 'view_preparationbatch', 'record_consumption'],
}


# --------------------------------------------------------------------------- roles
def role_of(user):
    """The user's role. Superusers (e.g. the original admin account) are Owners."""
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return OWNER
    return UserProfile.for_user(user).role


def is_owner(user):
    return role_of(user) == OWNER


def is_manager_or_owner(user):
    return role_of(user) in (OWNER, MANAGER)


def sync_roles(sender=None, **kwargs):
    """Create/refresh the three groups and their permissions. Idempotent; safe on every migrate.

    Also puts each existing user into the group that matches their profile role, without
    touching any other group or any per-user grants.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.apps import apps
    try:
        app_cts = ContentType.objects.filter(app_label='predictor')
        all_perms = Permission.objects.filter(content_type__in=app_cts)
        if not all_perms.exists():
            return  # permissions not created yet (very first migrate pass)
        by_code = {p.codename: p for p in all_perms}
        for role, codenames in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=GROUP_NAMES[role])
            perms = list(all_perms) if codenames is None else [by_code[c] for c in codenames if c in by_code]
            group.permissions.set(perms)
        for profile in UserProfile.objects.select_related('user'):
            apply_group(profile.user, profile.role)
    except Exception:  # pragma: no cover - never block migrate on a sync problem
        import logging
        logging.getLogger(__name__).exception('Role sync failed')


def apply_group(user, role):
    """Make `user` a member of exactly the one role group matching `role`."""
    role_groups = Group.objects.filter(name__in=GROUP_NAMES.values())
    keep = Group.objects.filter(name=GROUP_NAMES.get(role))
    user.groups.remove(*[g for g in role_groups if g not in keep])
    user.groups.add(*keep)


# ------------------------------------------------------------------ view protection
def permission_required(*perms, any_of=False):
    """Login + permission check on the server. Anonymous -> login page; no permission -> 403."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            checks = [user.has_perm(f'predictor.{p}') for p in perms]
            if not (any(checks) if any_of else all(checks)):
                raise PermissionDenied(MSG_DENIED)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def deny(request, message=MSG_DENIED, to='dashboard'):
    """Friendly refusal for actions inside a page: flash the message and go back."""
    messages.error(request, message)
    return redirect(to)


# ------------------------------------------------------------------- activity log
_SECRET = re.compile(r'pass|token|secret|key|csrf|otp|session', re.I)


def clean_values(values):
    """Drop anything that looks like a secret and make the rest JSON-safe."""
    if not values:
        return None
    out = {}
    for key, value in values.items():
        if _SECRET.search(str(key)):
            continue
        if isinstance(value, Decimal):
            value = f'{value:.2f}'
        out[key] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return out or None


def log_activity(user, action, obj=None, old=None, new=None, summary='', object_type=None, object_repr=None):
    label = ''
    if user is not None and getattr(user, 'is_authenticated', False):
        label = user.get_full_name() or user.get_username()
    return ActivityLog.objects.create(
        user=user if label else None, user_label=label[:150], action=action,
        object_type=object_type or (obj._meta.verbose_name if obj is not None else ''),
        object_id=str(obj.pk) if obj is not None and obj.pk else '',
        object_repr=(object_repr if object_repr is not None else (str(obj) if obj is not None else ''))[:200],
        old_values=clean_values(old), new_values=clean_values(new), summary=summary[:300])


def diff(before, after):
    """({changed: old}, {changed: new}) for two flat dicts."""
    changed = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}


def snapshot(obj, fields):
    return {f: getattr(obj, f) for f in fields}
