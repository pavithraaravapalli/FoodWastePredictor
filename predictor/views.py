from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.contrib.auth import update_session_auth_hash

from .forms import TrafficForm, FoodItemForm, FoodRecordForm, ProfileForm, RestaurantForm, StyledPasswordChangeForm
from .access import (MSG_DENIED, MSG_LOCKED, MSG_WAITING, diff, is_manager_or_owner, log_activity,
                     permission_required, role_of, snapshot)
from .models import (ActivityLog, FoodItem, FoodRecord, Prediction, RestaurantSettings, TrafficObservation,
                     UserProfile)
from django.http import Http404, HttpResponse
from django.conf import settings

from .services import analytics as analytics_svc
from .services import forecast as fc
from .services import reports as reports_svc
from .services import traffic as traffic_svc
from .services import weather as weather_svc
from .services import events as events_svc
from .services.traffic import daily_conditions
from .services.records import filter_items, filter_records, parse_date, records_summary
from .services.dashboard import build_dashboard


def _greeting():
    hour = timezone.localtime().hour
    return 'Good morning' if hour < 12 else 'Good afternoon' if hour < 17 else 'Good evening'


RECORD_FIELDS = ['food_item_id', 'date', 'prepared_quantity', 'sold_quantity', 'weather', 'is_holiday',
                 'event_name', 'status']


def _pending_qs():
    return FoodRecord.objects.filter(status=FoodRecord.STATUS_SUBMITTED).select_related('food_item', 'created_by')


@login_required
def dashboard(request):
    today = timezone.localdate()
    user, role = request.user, role_of(request.user)
    if role == UserProfile.STAFF:
        return _staff_dashboard(request, today)
    context = build_dashboard(request.GET, today)
    context.update({'today': today, 'greeting': _greeting(), 'role': role})
    if user.has_perm('predictor.approve_foodrecord'):
        pending = _pending_qs()
        context['pending_count'] = pending.count()
        context['pending_preview'] = list(pending.order_by('submitted_at', 'id')[:5])
    if role == UserProfile.OWNER:
        context['team'] = {
            'active': UserProfile.objects.filter(user__is_active=True).count(),
            'pending_signups': UserProfile.objects.filter(is_pending=True, user__is_active=False).count(),
        }
        context['recent_activity'] = list(ActivityLog.objects.all()[:5])
    return render(request, 'predictor/dashboard.html', context)


def _staff_dashboard(request, today):
    mine = FoodRecord.objects.filter(created_by=request.user).select_related('food_item')
    plan = Prediction.objects.filter(date=today, status__in=[Prediction.STATUS_APPROVED, Prediction.STATUS_PREPARED]) \
        .select_related('food_item')
    return render(request, 'predictor/staff_dashboard.html', {
        'today': today, 'greeting': _greeting(), 'role': UserProfile.STAFF,
        'plan_lines': list(plan),
        'recent': list(mine.order_by('-date', '-id')[:8]),
        'today_done': set(mine.filter(date=today).values_list('food_item_id', flat=True)),
        'rejected': list(mine.filter(status=FoodRecord.STATUS_REJECTED).order_by('-updated_at')[:5]),
        'counts': {k: mine.filter(status=k).count() for k, _ in FoodRecord.STATUS_CHOICES},
        'items': FoodItem.objects.filter(is_active=True),
    })


def _apply_workflow(request, record, creating):
    """Set status/audit fields for a record being saved by `request.user`.

    Approvers (Owner/Manager) save straight to Approved. Everyone else saves a Draft
    or Submits for review, chosen by the button they pressed.
    """
    user, now = request.user, timezone.now()
    if creating:
        record.created_by = user
    record.updated_by = user
    if user.has_perm('predictor.approve_foodrecord'):
        if creating or record.status in (FoodRecord.STATUS_DRAFT, FoodRecord.STATUS_REJECTED):
            record.status, record.approved_by, record.approved_at = FoodRecord.STATUS_APPROVED, user, now
        return 'approved'
    if request.POST.get('submit_action') == 'draft':
        record.status = FoodRecord.STATUS_DRAFT
        return 'draft'
    record.status, record.submitted_at = FoodRecord.STATUS_SUBMITTED, now
    record.review_note, record.approved_by, record.approved_at = '', None, None
    return 'submitted'


def _save_message(request, record, outcome):
    label = f'{record.food_item.name} for {record.date:%d-%m-%Y}'
    if outcome == 'submitted':
        messages.success(request, f'Your record has been submitted successfully. {MSG_WAITING}')
    elif outcome == 'draft':
        messages.success(request, f'Draft saved for {label}. Submit it when you are ready.')
    else:
        messages.success(request, f'Saved {label}.')


@permission_required('add_foodrecord')
def add_record(request):
    if request.method == 'POST':
        form = FoodRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            outcome = _apply_workflow(request, record, creating=True)
            record.save()
            log_activity(request.user, 'record_submit' if outcome == 'submitted' else 'record_create', record,
                         new=snapshot(record, RECORD_FIELDS), summary=f'Record {outcome}')
            _save_message(request, record, outcome)
            return redirect('dashboard' if outcome != 'draft' else 'records')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    else:
        initial = {'date': parse_date(request.GET.get('date')) or timezone.localdate()}
        # Prefill links from the Preparation Planner ("Log results").
        if request.GET.get('item', '').isdigit():
            initial['food_item'] = int(request.GET['item'])
        if request.GET.get('prepared', '').isdigit():
            initial['prepared_quantity'] = int(request.GET['prepared'])
        form = FoodRecordForm(initial=initial)

    return render(request, 'predictor/add_record.html', {
        'form': form, 'is_approver': request.user.has_perm('predictor.approve_foodrecord')})


# --- Food inventory ----------------------------------------------------------

@permission_required('view_fooditem')
def inventory(request):
    items, filters = filter_items(request.GET)
    categories = (FoodItem.objects.order_by().values_list('category', flat=True).distinct().order_by('category'))
    return render(request, 'predictor/inventory.html', {
        'items': items, 'filters': filters, 'categories': categories,
        'total_items': FoodItem.objects.count(),
        'filtered': bool(filters['q'] or filters['category'] or filters['status'] != 'active'),
    })


def _item_form_page(request, form, item=None):
    categories = FoodItem.objects.order_by().values_list('category', flat=True).distinct()
    return render(request, 'predictor/food_item_form.html', {'form': form, 'item': item, 'categories': categories})


@permission_required('add_fooditem')
def food_item_add(request):
    form = FoodItemForm(request.POST or None, can_set_price=request.user.has_perm('predictor.change_item_price'))
    if request.method == 'POST':
        if form.is_valid():
            item = form.save()
            log_activity(request.user, 'item_create', item, new=snapshot(item, ['name', 'category', 'cost_per_portion']))
            messages.success(request, f'Added {item.name} to your inventory.')
            return redirect('inventory')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return _item_form_page(request, form)


@permission_required('change_fooditem')
def food_item_edit(request, pk):
    item = get_object_or_404(FoodItem, pk=pk)
    form = FoodItemForm(request.POST or None, instance=item,
                        can_set_price=request.user.has_perm('predictor.change_item_price'))
    if request.method == 'POST':
        before = snapshot(item, ['name', 'category', 'cost_per_portion'])
        if form.is_valid():
            form.save()
            old, new = diff(before, snapshot(item, ['name', 'category', 'cost_per_portion']))
            log_activity(request.user, 'item_update', item, old=old, new=new)
            messages.success(request, f'Updated {item.name}.')
            return redirect('inventory')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return _item_form_page(request, form, item)


@permission_required('change_fooditem')
@require_POST
def food_item_toggle(request, pk):
    item = get_object_or_404(FoodItem, pk=pk)
    item.is_active = not item.is_active
    item.save(update_fields=['is_active'])
    log_activity(request.user, 'item_toggle', item, old={'is_active': not item.is_active},
                 new={'is_active': item.is_active})
    messages.success(request, f"{item.name} is now {'active' if item.is_active else 'inactive'}."
                              + ('' if item.is_active else ' Its history is kept.'))
    return redirect(request.POST.get('next') or 'inventory') if _safe(request) else redirect('inventory')


@permission_required('delete_fooditem')
@require_POST
def food_item_delete(request, pk):
    item = get_object_or_404(FoodItem, pk=pk)
    count = item.foodrecord_set.count()
    if count:
        messages.error(request, f'{item.name} has {count} daily record{"s" if count != 1 else ""}, so it can\'t be deleted. '
                                'Deactivate it instead to keep your history.')
    else:
        log_activity(request.user, 'item_delete', item, old=snapshot(item, ['name', 'category']))
        item.delete()
        messages.success(request, f'Deleted {item.name}.')
    return redirect('inventory')


def _safe(request):
    from django.utils.http import url_has_allowed_host_and_scheme
    target = request.POST.get('next') or ''
    return (not target) or url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()})


# --- Daily records -----------------------------------------------------------

@permission_required('view_foodrecord')
def records(request):
    user = request.user
    is_staff_role = role_of(user) == UserProfile.STAFF
    qs, filters = filter_records(request.GET, only_user=user if is_staff_role else None)
    page = Paginator(qs, 15).get_page(request.GET.get('page'))
    keep = {k: v for k, v in filters.items() if v}
    from urllib.parse import urlencode
    scope = FoodRecord.objects.filter(created_by=user) if is_staff_role else FoodRecord.objects.all()
    return render(request, 'predictor/records.html', {
        'page': page, 'filters': filters, 'summary': records_summary(qs.filter(status=FoodRecord.STATUS_APPROVED)),
        'items': FoodItem.objects.all(), 'threshold': RestaurantSettings.load().waste_alert_threshold,
        'querystring': urlencode(keep), 'filtered': bool(keep),
        'has_any': scope.exists(), 'own_only': is_staff_role,
        'status_choices': FoodRecord.STATUS_CHOICES,
        'show_money': user.has_perm('predictor.view_analytics'),
        'current_url': request.get_full_path(),
    })


def _can_edit(user, record):
    """(allowed, reason). Approvers edit any record; others only their OWN unapproved ones."""
    if user.has_perm('predictor.change_foodrecord'):
        return True, ''
    if record.created_by_id != user.pk:
        return False, MSG_DENIED
    if record.status == FoodRecord.STATUS_APPROVED:
        return False, MSG_LOCKED
    if record.status == FoodRecord.STATUS_SUBMITTED:
        return False, f'This record has been submitted. {MSG_WAITING}'
    return True, ''


@permission_required('view_foodrecord')
def record_edit(request, pk):
    record = get_object_or_404(FoodRecord.objects.select_related('food_item'), pk=pk)
    allowed, reason = _can_edit(request.user, record)
    if not allowed:
        if reason == MSG_DENIED:
            raise PermissionDenied(reason)
        messages.warning(request, reason)
        return redirect('records')
    before = snapshot(record, RECORD_FIELDS)
    form = FoodRecordForm(request.POST or None, instance=record)
    if request.method == 'POST':
        if form.is_valid():
            record = form.save(commit=False)
            outcome = _apply_workflow(request, record, creating=False)
            record.save()
            old, new = diff(before, snapshot(record, RECORD_FIELDS))
            log_activity(request.user, 'record_submit' if outcome == 'submitted' else 'record_update', record, old=old, new=new)
            _save_message(request, record, outcome)
            return redirect('records')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return render(request, 'predictor/add_record.html', {
        'form': form, 'record': record, 'heading': 'Edit daily record', 'submit_label': 'Save changes',
        'is_approver': request.user.has_perm('predictor.approve_foodrecord')})


@permission_required('delete_foodrecord')
@require_POST
def record_delete(request, pk):
    record = get_object_or_404(FoodRecord.objects.select_related('food_item'), pk=pk)
    label = f'{record.food_item.name} on {record.date:%d-%m-%Y}'
    log_activity(request.user, 'record_delete', record, old=snapshot(record, RECORD_FIELDS), object_repr=label)
    record.delete()
    messages.success(request, f'Deleted the record for {label}.')
    target = request.POST.get('next') or ''
    return redirect(target) if target and _safe(request) else redirect('records')


# --- Approvals -----------------------------------------------------------------

@permission_required('approve_foodrecord')
def approvals(request):
    qs = _pending_qs().order_by('submitted_at', 'id')
    page = Paginator(qs, 15).get_page(request.GET.get('page'))
    return render(request, 'predictor/approvals.html', {'page': page, 'total': qs.count()})


@permission_required('approve_foodrecord')
@require_POST
def record_review(request, pk):
    record = get_object_or_404(FoodRecord.objects.select_related('food_item'), pk=pk)
    action = request.POST.get('action')
    note = (request.POST.get('note') or '').strip()[:300]
    if record.status != FoodRecord.STATUS_SUBMITTED:
        messages.warning(request, 'This record is not waiting for review any more.')
        return redirect('approvals')
    label = f'{record.food_item.name} for {record.date:%d-%m-%Y}'
    before = snapshot(record, ['status', 'review_note'])
    if action == 'approve':
        record.status, record.approved_by, record.approved_at = FoodRecord.STATUS_APPROVED, request.user, timezone.now()
        record.review_note = note
        kind, text = 'record_approve', f'Approved {label}.'
    elif action == 'reject':
        if not note:
            messages.error(request, 'Please say why the record is rejected so the author can fix it.')
            return redirect('approvals')
        record.status, record.approved_by, record.approved_at = FoodRecord.STATUS_REJECTED, request.user, timezone.now()
        record.review_note = note
        kind, text = 'record_reject', f'Rejected {label}. The author can edit and resubmit it.'
    else:
        messages.error(request, 'Unknown action.')
        return redirect('approvals')
    record.updated_by = request.user
    record.save()
    old, new = diff(before, snapshot(record, ['status', 'review_note']))
    log_activity(request.user, kind, record, old=old, new=new)
    messages.success(request, text)
    return redirect('approvals')


# --- Notifications ---------------------------------------------------------------

@login_required
def notifications(request):
    user = request.user
    ctx = {'pending': [], 'rejected': [], 'approved': [], 'plan': [], 'pending_signups': 0}
    if user.has_perm('predictor.manage_users'):
        ctx['pending_signups'] = UserProfile.objects.filter(is_pending=True).count()
    if user.has_perm('predictor.approve_foodrecord'):
        ctx['pending'] = list(_pending_qs().order_by('submitted_at')[:20])
    mine = FoodRecord.objects.filter(created_by=user).select_related('food_item')
    ctx['rejected'] = list(mine.filter(status=FoodRecord.STATUS_REJECTED).order_by('-updated_at')[:20])
    since = timezone.now() - timedelta(days=7)
    ctx['approved'] = list(mine.filter(status=FoodRecord.STATUS_APPROVED, approved_at__gte=since).order_by('-approved_at')[:20])
    ctx['plan'] = list(Prediction.objects.filter(
        date=timezone.localdate(), status__in=[Prediction.STATUS_APPROVED, Prediction.STATUS_PREPARED]
    ).select_related('food_item'))
    return render(request, 'predictor/notifications.html', ctx)


# --- Activity log -----------------------------------------------------------------

@permission_required('view_activitylog')
def activity_log(request):
    qs = ActivityLog.objects.all()
    action = request.GET.get('action', '')
    if action in dict(ActivityLog.ACTIONS):
        qs = qs.filter(action=action)
    else:
        action = ''
    who = request.GET.get('user', '')
    if who.isdigit():
        qs = qs.filter(user_id=int(who))
    else:
        who = ''
    start, end = parse_date(request.GET.get('start')), parse_date(request.GET.get('end'))
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    from django.contrib.auth import get_user_model
    from urllib.parse import urlencode
    keep = {k: v for k, v in {'action': action, 'user': who, 'start': request.GET.get('start') if start else '',
                              'end': request.GET.get('end') if end else ''}.items() if v}
    return render(request, 'predictor/activity_log.html', {
        'page': Paginator(qs, 25).get_page(request.GET.get('page')),
        'actions': ActivityLog.ACTIONS, 'users': get_user_model().objects.order_by('username'),
        'filters': {'action': action, 'user': who, 'start': keep.get('start', ''), 'end': keep.get('end', '')},
        'querystring': urlencode(keep), 'filtered': bool(keep)})


# --- Demand forecast and preparation planner ---------------------------------

def _forecast_inputs(params):
    inputs = {
        'date': parse_date(params.get('date')) or timezone.localdate() + timedelta(days=1),
        'item': params.get('item') or 'all',
        'weather': (params.get('weather') or '').strip()[:50],
        'holiday': params.get('holiday') in ('1', 'on', 'true'),
        'event': (params.get('event') or '').strip()[:100],
        'traffic': params.get('traffic') if params.get('traffic') in dict(TrafficObservation.CONDITIONS) else '',
        'weather_source': '', 'event_source': '', 'traffic_source': '',
    }
    # First visit only: fill blanks from stored weather/events. Once the form has been submitted the
    # person's own values (even blank ones) are respected. Failures here never stop the forecast.
    if 'generate' not in params and 'weather' not in params:
        try:
            restaurant = RestaurantSettings.load()
            wx = weather_svc.for_date(inputs['date'], restaurant)
            if wx and wx.condition != 'other':
                inputs['weather'] = wx.get_condition_display()
                inputs['weather_source'] = f"{'automatic forecast' if wx.is_automatic else 'manual entry'} ({wx.observed_at:%d-%m-%Y %H:%M})"
            todays = events_svc.for_date(inputs['date'], restaurant)
            if todays and not inputs['event']:
                inputs['event'] = todays[0].name[:100]
                inputs['event_source'] = ', '.join(e.name for e in todays[:3]) + (' (' + ('automatic' if todays[0].is_automatic else 'manual') + ')')
        except Exception:                                        # noqa: BLE001 - context data is optional
            inputs['weather_source'] = inputs['event_source'] = ''
    return inputs


def _run_forecast(inputs):
    items = FoodItem.objects.filter(is_active=True)
    if inputs['item'] != 'all':
        items = items.filter(pk=inputs['item']) if str(inputs['item']).isdigit() else items.none()
    return fc.forecast_many(items, inputs['date'], weather=inputs['weather'],
                            is_holiday=inputs['holiday'], event=inputs['event'], traffic=inputs['traffic'])


@permission_required('generate_predictions')
def forecast(request):
    inputs = _forecast_inputs(request.GET)
    generated = 'generate' in request.GET
    results = _run_forecast(inputs) if generated else []
    single = results[0] if len(results) == 1 and results[0].ok else None
    existing = {p.food_item_id: p for p in Prediction.objects.filter(date=inputs['date'])} if generated else {}
    for r in results:
        r.saved = existing.get(r.item.pk)
    ingredient_ctx = {}
    if generated and any(r.ok for r in results):
        ingredient_ctx = _requirements_context(
            request.user, {r.item: r.recommended for r in results if r.ok},
            {r.item: r.expected for r in results if r.ok}, request.GET,
            {k: v for k, v in request.GET.items() if k != 'reserve'})
    return render(request, 'predictor/forecast.html', {
        **ingredient_ctx,
        'inputs': inputs, 'generated': generated, 'results': results, 'single': single,
        'items': FoodItem.objects.filter(is_active=True),
        'has_records': FoodRecord.objects.exists(),
        'any_ok': any(r.ok for r in results),
        'min_sample': fc.MIN_SAMPLE, 'traffic_choices': TrafficObservation.CONDITIONS,
        'traffic_days': len(daily_conditions()),
        'selected_item': inputs['item'],
        'tomorrow': timezone.localdate() + timedelta(days=1),
    })


@permission_required('generate_predictions')
@require_POST
def forecast_save(request):
    """Re-computes on the server (never trusts posted numbers) and saves to the plan."""
    inputs = _forecast_inputs(request.POST)
    saved, protected, no_data = fc.save_forecasts(_run_forecast(inputs))
    parts = []
    if saved:
        parts.append(f'Saved {saved} forecast{"s" if saved != 1 else ""} to the plan for {inputs["date"]:%d-%m-%Y}.')
    if protected:
        parts.append(f'{protected} already approved or prepared, so left unchanged.')
    if no_data:
        parts.append(f'{no_data} skipped for lack of history.')
    if saved:
        log_activity(request.user, 'prediction', summary=f'Saved {saved} forecast(s) for {inputs["date"]:%d-%m-%Y}',
                     object_type='Prediction', new={'date': inputs['date'], 'saved': saved})
    (messages.success if saved else messages.warning)(request, ' '.join(parts) or 'Nothing to save.')
    return redirect(f"{reverse('planner')}?date={inputs['date'].isoformat()}")


RESERVE_OPTIONS = [0, 5, 10, 15, 20]


def _reserve_pct(params):
    raw = params.get('reserve') or '0'
    return int(raw) if raw.isdigit() and int(raw) in RESERVE_OPTIONS else 0


def _requirements_context(user, quantities, demand, params, keep):
    """Stage 2 of the forecast: ingredient needs for the given {food item: portions}. None if not allowed."""
    if not user.has_perm('predictor.view_ingredient'):
        return {}
    from .services import requirements as req_svc
    pct = _reserve_pct(params)
    req = req_svc.calculate(quantities, pct)
    for row in req['items']:
        row['demand'] = demand.get(row['item'])
    return {'req': req, 'reserve_pct': pct, 'reserve_options': RESERVE_OPTIONS, 'keep': keep,
            'show_cost': user.has_perm('predictor.view_reports')}


@permission_required('view_prediction')
def planner(request):
    today = timezone.localdate()
    chosen = parse_date(request.GET.get('date'))
    if chosen is None:
        upcoming = Prediction.objects.filter(date__gte=today).order_by('date').values_list('date', flat=True).first()
        chosen = upcoming or today
    can_manage = request.user.has_perm('predictor.manage_plans')
    plan_qs = Prediction.objects.filter(date=chosen).select_related('food_item', 'approved_by')
    if not can_manage:   # Staff only ever see plans a manager has approved
        plan_qs = plan_qs.filter(status__in=[Prediction.STATUS_APPROVED, Prediction.STATUS_PREPARED])
    lines = list(plan_qs)
    counts = {k: sum(1 for l in lines if l.status == k) for k in ('suggested', 'approved', 'prepared')}
    ctx = {
        'date': chosen, 'lines': lines, 'counts': counts,
        'prev_date': chosen - timedelta(days=1), 'next_date': chosen + timedelta(days=1),
        'is_today': chosen == today, 'can_manage': can_manage,
        'can_batch': request.user.has_perm('predictor.add_preparationbatch'),
    }
    if can_manage and lines:
        ctx.update(_requirements_context(
            request.user, {l.food_item: l.quantity_to_prepare for l in lines},
            {l.food_item: l.predicted_demand for l in lines}, request.GET, {'date': chosen.isoformat()}))
    return render(request, 'predictor/planner.html', ctx)


def _plan_redirect(date):
    return redirect(f"{reverse('planner')}?date={date.isoformat()}")


@permission_required('manage_plans')
@require_POST
def planner_action(request, pk):
    line = get_object_or_404(Prediction.objects.select_related('food_item'), pk=pk)
    action = request.POST.get('action')
    name = line.food_item.name
    if action == 'approve':
        raw = (request.POST.get('quantity') or '').strip()
        qty = int(raw) if raw.isdigit() else None
        if qty is None or qty > 100000:
            messages.error(request, 'Enter a whole number of portions (0 or more) to approve.')
        else:
            line.status, line.approved_quantity = Prediction.STATUS_APPROVED, qty
            line.approved_by, line.approved_at = request.user, timezone.now()
            line.save()
            messages.success(request, f'Approved {qty} portions of {name}.')
    elif action == 'prepared':
        if line.status != Prediction.STATUS_APPROVED:
            messages.error(request, f'{name} must be approved before it is marked prepared.')
        else:
            line.status = Prediction.STATUS_PREPARED
            line.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'{name} marked as prepared.')
    elif action == 'reopen':
        line.status, line.approved_quantity, line.approved_by, line.approved_at = Prediction.STATUS_SUGGESTED, None, None, None
        line.save()
        messages.success(request, f'{name} is back to a suggestion.')
    else:
        messages.error(request, 'Unknown action.')
    if action in ('approve', 'prepared', 'reopen'):
        log_activity(request.user, 'plan_update', line, new={'action': action, 'status': line.status,
                                                          'approved_quantity': line.approved_quantity})
    return _plan_redirect(line.date)


@permission_required('manage_plans')
@require_POST
def planner_approve_all(request):
    date = parse_date(request.POST.get('date')) or timezone.localdate()
    lines = Prediction.objects.filter(date=date, status=Prediction.STATUS_SUGGESTED)
    n = 0
    for line in lines:
        line.status, line.approved_quantity = Prediction.STATUS_APPROVED, line.recommended_preparation
        line.approved_by, line.approved_at = request.user, timezone.now()
        line.save()
        n += 1
    if n:
        log_activity(request.user, 'plan_update', summary=f'Approved {n} suggestion(s) for {date:%d-%m-%Y}',
                     object_type='Prediction', new={'date': date, 'approved': n})
    messages.success(request, f'Approved {n} suggestion{"s" if n != 1 else ""} at the recommended quantities.' if n else 'Nothing to approve.')
    return _plan_redirect(date)


# --- Settings / profile ------------------------------------------------------

@login_required
def settings_view(request):
    user, restaurant = request.user, RestaurantSettings.load()
    profile = UserProfile.for_user(user)
    tab = request.GET.get('tab', 'profile')
    can_restaurant = user.has_perm('predictor.change_restaurantsettings')
    forms_ = {
        'profile': ProfileForm(user=user, initial={
            'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email,
            'phone': profile.phone}),
        'restaurant': RestaurantForm(instance=restaurant),
        'password': StyledPasswordChangeForm(user),
    }
    if not can_restaurant:
        del forms_['restaurant']
    can_integrations = user.has_perm('predictor.manage_users')
    if request.method == 'POST':
        which = request.POST.get('form')
        if which == 'profile':
            form = ProfileForm(request.POST, user=user)
        elif which == 'restaurant':
            if not can_restaurant:
                raise PermissionDenied(MSG_DENIED)
            form = RestaurantForm(request.POST, instance=restaurant)
        elif which == 'password':
            form = StyledPasswordChangeForm(user, request.POST)
        else:
            return redirect('settings')
        if form.is_valid():
            if which == 'password':
                user = form.save()
                update_session_auth_hash(request, user)       # stay signed in
                messages.success(request, 'Password changed.')
            else:
                if which == 'restaurant':
                    before = snapshot(restaurant, list(form.fields))
                form.save()
                if which == 'restaurant':
                    old, new = diff(before, snapshot(restaurant, list(form.fields)))
                    if new:
                        log_activity(request.user, 'settings', restaurant, old=old, new=new)
                messages.success(request, 'Profile updated.' if which == 'profile' else 'Restaurant details saved.')
            return redirect(f"{reverse('settings')}?tab={which}")
        forms_[which] = form
        tab = which
        messages.error(request, 'Please fix the highlighted fields and try again.')
    if tab not in forms_:
        tab = 'profile'
    return render(request, 'predictor/settings.html', {
        'forms': forms_, 'tab': tab, 'profile': profile, 'restaurant_obj': restaurant,
        'has_password': user.has_usable_password(), 'can_restaurant': can_restaurant,
        'can_integrations': can_integrations,
         'email_configured': settings.EMAIL_CONFIGURED, 'avatar_tone': user.pk % 5,
        'record_count': FoodRecord.objects.count(), 'item_count': FoodItem.objects.filter(is_active=True).count(),
    })


# --- Waste analytics ---------------------------------------------------------

@permission_required('view_analytics')
def analytics(request):
    ctx = analytics_svc.build_analytics(request.GET, timezone.localdate())
    return render(request, 'predictor/analytics.html', ctx)


# --- Reports -----------------------------------------------------------------

def _report_query(request):
    kind = request.GET.get('kind', 'week')
    return kind if kind in reports_svc.KINDS else 'week'


@permission_required('view_reports')
def reports(request):
    ctx = reports_svc.build_report(_report_query(request), request.GET, timezone.localdate())
    ctx['currency'] = RestaurantSettings.load().currency_symbol
    ctx['threshold'] = RestaurantSettings.load().waste_alert_threshold
    return render(request, 'predictor/reports.html', ctx)


@permission_required('view_reports')
def reports_export(request):
    csv_type = request.GET.get('type')
    if csv_type not in reports_svc.CSV_TYPES:
        raise Http404('Unknown export type')
    kind, start, end, label, _, _ = reports_svc.period_bounds(_report_query(request), request.GET, timezone.localdate())
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="foodwaste-{csv_type}-{start:%Y%m%d}-{end:%Y%m%d}.csv"'
    response.write('\ufeff')                      # BOM so Excel reads ₹ and accents correctly
    reports_svc.write_csv(response, csv_type, start, end)
    return response


# --- Traffic intelligence ----------------------------------------------------

@permission_required('view_trafficobservation')
def traffic(request):
    restaurant = RestaurantSettings.load()
    fetch_error = traffic_svc.ensure_fresh(restaurant)           # automatic mode only; never breaks the page
    observations = list(TrafficObservation.objects.select_related('created_by')[:15])
    now = timezone.localtime()
    form = TrafficForm(initial={'observed_at': now.strftime('%Y-%m-%dT%H:%M'), 'condition': 'moderate'})
    return render(request, 'predictor/traffic.html', {
        'restaurant_obj': restaurant, 'observations': observations, 'latest': observations[0] if observations else None,
        'form': form, 'api_enabled': settings.TRAFFIC_ENABLED, 'provider': settings.TRAFFIC_PROVIDER_NAME,
        'days_recorded': len(traffic_svc.daily_conditions()),
        'now_local': now, 'fetch_error': fetch_error,
        'mode': restaurant.traffic_mode, 'mode_key': 'traffic',
        'can_switch_mode': request.user.has_perm('predictor.manage_data_sources'),
        'latest_stale': bool(observations) and (timezone.now() - observations[0].observed_at) > timedelta(hours=3),
    })


@permission_required('add_trafficobservation')
@require_POST
def traffic_add(request):
    form = TrafficForm(request.POST)
    if form.is_valid():
        obs = form.save(commit=False)
        obs.source, obs.created_by = TrafficObservation.SOURCE_MANUAL, request.user
        obs.save()
        messages.success(request, f'Recorded {obs.get_condition_display().lower()} traffic.')
    else:
        problems = '; '.join(e for errs in form.errors.values() for e in errs)
        messages.error(request, f'Could not save the traffic entry: {problems}')
    return redirect('traffic')


@permission_required('add_trafficobservation')
@require_POST
def traffic_fetch(request):
    restaurant = RestaurantSettings.load()
    if not settings.TRAFFIC_ENABLED:
        messages.error(request, 'No traffic provider is configured. Enter traffic by hand instead.')
    elif not restaurant.has_location:
        messages.error(request, 'Set your restaurant location in Settings first.')
    else:
        try:
            obs, note = traffic_svc.refresh(restaurant, user=request.user, force=True)
        except traffic_svc.TrafficError as e:
            messages.error(request, str(e))
        else:
            if obs is None:
                messages.info(request, note)
            else:
                messages.success(request, f"Live reading saved: {obs.get_condition_display().lower()} "
                                          f"({obs.current_speed} km/h vs {obs.free_flow_speed} km/h free flow).")
    return redirect('traffic')


@permission_required('delete_trafficobservation')
@require_POST
def traffic_delete(request, pk):
    get_object_or_404(TrafficObservation, pk=pk).delete()
    messages.success(request, 'Traffic entry deleted.')
    return redirect('traffic')
