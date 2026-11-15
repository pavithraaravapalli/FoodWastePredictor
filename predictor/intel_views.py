"""Weather, local events and the automatic/manual switch for each outside-world input."""
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import log_activity, permission_required, snapshot, diff
from .intel_forms import LocalEventForm, WeatherForm
from .models import LocalEvent, RestaurantSettings, WeatherObservation
from .services import events as events_svc
from .services import weather as weather_svc
from .services.http import ProviderError

MODE_FIELDS = {'weather': 'weather_mode', 'traffic': 'traffic_mode', 'events': 'events_mode'}
MODE_RETURN = {'weather': 'weather', 'traffic': 'traffic', 'events': 'events'}


@permission_required('manage_data_sources')
@require_POST
def data_mode_set(request, which):
    field = MODE_FIELDS.get(which)
    mode = request.POST.get('mode')
    if field is None or mode not in (RestaurantSettings.MODE_AUTO, RestaurantSettings.MODE_MANUAL):
        messages.error(request, 'Unknown data source setting.')
        return redirect('dashboard')
    restaurant = RestaurantSettings.load()
    old = getattr(restaurant, field)
    setattr(restaurant, field, mode)
    restaurant.save(update_fields=[field])
    if old != mode:
        log_activity(request.user, 'settings', restaurant, old={field: old}, new={field: mode},
                     summary=f'{which.title()} data set to {mode}')
    messages.success(request, f'{which.title()} is now in {"automatic" if mode == "auto" else "manual"} mode.')
    return redirect(MODE_RETURN[which])


# --- Weather -------------------------------------------------------------------

@permission_required('view_weatherobservation')
def weather(request):
    restaurant = RestaurantSettings.load()
    fetch_error = weather_svc.ensure_fresh(restaurant)              # automatic mode only; never breaks the page
    today = timezone.localdate()
    current = weather_svc.latest_current()
    current_state, current_msg = weather_svc.status(current)
    outlook = weather_svc.forecast_rows(today)
    manual_days = WeatherObservation.objects.filter(source='manual', target_date__gte=today).order_by('target_date', '-id')
    page = Paginator(WeatherObservation.objects.filter(kind='current').select_related('created_by'), 15).get_page(request.GET.get('page'))
    return render(request, 'predictor/weather.html', {
        'restaurant_obj': restaurant, 'mode': restaurant.weather_mode, 'mode_key': 'weather',
        'can_switch_mode': request.user.has_perm('predictor.manage_data_sources'),
        'can_add': request.user.has_perm('predictor.add_weatherobservation'),
        'form': WeatherForm(initial={'target_date': today}), 'current': current, 'current_state': current_state,
        'current_msg': current_msg, 'outlook': outlook, 'manual_days': manual_days[:10], 'page': page,
        'fetch_error': fetch_error, 'provider': weather_svc.PROVIDER,
    })


@permission_required('add_weatherobservation')
@require_POST
def weather_add(request):
    form = WeatherForm(request.POST)
    if not form.is_valid():
        problems = '; '.join(e for errs in form.errors.values() for e in errs)
        messages.error(request, f'Could not save the weather entry: {problems}')
        return redirect('weather')
    obs = form.save(commit=False)
    now = timezone.now()
    obs.source, obs.provider, obs.observed_at, obs.created_by = 'manual', '', now, request.user
    obs.kind = 'current' if obs.target_date == timezone.localdate() else 'forecast'
    obs.save()                                                      # always a new row: history is never overwritten
    log_activity(request.user, 'weather', obs, new={'day': obs.target_date, 'condition': obs.condition,
                                                    'temperature': obs.temperature_c}, summary='Weather entered manually')
    messages.success(request, f'Saved {obs.get_condition_display().lower()} weather for {obs.target_date:%d-%m-%Y}.')
    return redirect('weather')


@permission_required('add_weatherobservation')
@require_POST
def weather_refresh(request):
    restaurant = RestaurantSettings.load()
    try:
        added, note = weather_svc.refresh(restaurant, force=True)
    except ProviderError as e:
        messages.error(request, str(e))
    else:
        (messages.success if added else messages.info)(request, note)
    return redirect('weather')


# --- Local events ----------------------------------------------------------------

def _event_filters(params):
    return {'q': (params.get('q') or '').strip(), 'category': params.get('category') or '',
            'show': 'all' if params.get('show') == 'all' else 'upcoming'}


@permission_required('view_localevent')
def events(request):
    restaurant = RestaurantSettings.load()
    fetch_error = events_svc.ensure_fresh(restaurant)
    filters = _event_filters(request.GET)
    qs = LocalEvent.objects.select_related('created_by')
    if filters['show'] == 'upcoming':
        qs = qs.filter(Q(end_at__gte=timezone.now()) | Q(end_at__isnull=True, start_at__gte=timezone.now() - timedelta(hours=12)))
    if filters['q']:
        qs = qs.filter(Q(name__icontains=filters['q']) | Q(venue__icontains=filters['q']))
    if filters['category'] in dict(LocalEvent.CATEGORIES):
        qs = qs.filter(category=filters['category'])
    else:
        filters['category'] = ''
    manual, auto = qs.filter(source='manual'), qs.filter(source='auto')
    last = LocalEvent.objects.filter(source='auto').order_by('-fetched_at').values_list('fetched_at', flat=True).first()
    return render(request, 'predictor/events.html', {
        'restaurant_obj': restaurant, 'mode': restaurant.events_mode, 'mode_key': 'events',
        'can_switch_mode': request.user.has_perm('predictor.manage_data_sources'),
        'can_add': request.user.has_perm('predictor.add_localevent'),
        'can_change': request.user.has_perm('predictor.change_localevent'),
        'can_delete': request.user.has_perm('predictor.delete_localevent'),
        'manual': Paginator(manual, 10).get_page(request.GET.get('page')), 'auto': auto[:30],
        'filters': filters, 'categories': LocalEvent.CATEGORIES, 'api_enabled': events_svc.settings.EVENTS_ENABLED,
        'provider': events_svc.settings.EVENTS_PROVIDER_NAME, 'last_fetch': last, 'fetch_error': fetch_error,
        'stale': bool(last) and timezone.now() - last > events_svc.STALE_AFTER,
        'filtered': bool(filters['q'] or filters['category'] or filters['show'] == 'all'),
    })


def _event_page(request, form, event=None):
    return render(request, 'predictor/event_form.html', {'form': form, 'event': event})


@permission_required('add_localevent')
def event_add(request):
    form = LocalEventForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            ev = form.save(commit=False)
            ev.source, ev.created_by = 'manual', request.user
            ev.save()
            log_activity(request.user, 'event', ev, new={'name': ev.name, 'start': ev.start_at}, summary='Event added manually')
            messages.success(request, f'Added {ev.name}.')
            return redirect('events')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return _event_page(request, form)


@permission_required('change_localevent')
def event_edit(request, pk):
    ev = get_object_or_404(LocalEvent, pk=pk, source='manual')       # automatic events come from the provider: not editable
    form = LocalEventForm(request.POST or None, instance=ev)
    if request.method == 'POST':
        before = snapshot(ev, ['name', 'category', 'start_at', 'end_at', 'venue'])
        if form.is_valid():
            form.save()
            old, new = diff(before, snapshot(ev, list(before)))
            if new:
                log_activity(request.user, 'event', ev, old=old, new=new, summary='Event edited')
            messages.success(request, f'Updated {ev.name}.')
            return redirect('events')
        messages.error(request, 'Please fix the highlighted fields and try again.')
    return _event_page(request, form, ev)


@permission_required('change_localevent')
@require_POST
def event_toggle(request, pk):
    ev = get_object_or_404(LocalEvent, pk=pk)                         # either kind can be hidden from forecasts
    ev.is_active = not ev.is_active
    ev.save(update_fields=['is_active'])
    log_activity(request.user, 'event', ev, new={'is_active': ev.is_active}, summary='Event activated' if ev.is_active else 'Event deactivated')
    messages.success(request, f"{ev.name} is now {'active' if ev.is_active else 'inactive and ignored by forecasts'}.")
    return redirect('events')


@permission_required('delete_localevent')
@require_POST
def event_delete(request, pk):
    ev = get_object_or_404(LocalEvent, pk=pk, source='manual')
    name = ev.name
    log_activity(request.user, 'event', ev, old={'name': name, 'start': ev.start_at}, summary='Event removed')
    ev.delete()
    messages.success(request, f'Removed {name}.')
    return redirect('events')


@permission_required('add_localevent')
@require_POST
def events_refresh(request):
    try:
        note = events_svc.refresh(RestaurantSettings.load(), force=True)
    except ProviderError as e:
        messages.error(request, str(e))
    else:
        messages.info(request, note)
    return redirect('events')
