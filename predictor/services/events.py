"""Local events: automatic discovery (Ticketmaster Discovery API) plus manual entries.

Coverage warning: Ticketmaster's public Discovery API has good coverage in North America, Europe,
Australia and a few other countries, and little or none in much of India. When it returns nothing we
say "no matching events were returned" - never "there are no events". Manual entry always works.
"""
import re
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from ..models import LocalEvent, RestaurantSettings
from .http import ProviderError, get_json

PROVIDER = 'Ticketmaster'
URL = 'https://app.ticketmaster.com/discovery/v2/events.json'
LOOKAHEAD_DAYS = 14
REFRESH_TTL = timedelta(hours=6)
STALE_AFTER = timedelta(hours=24)
CACHE_KEY = 'events:last-check'


def _category(event):
    segment = ''
    try:
        cls = (event.get('classifications') or [{}])[0]
        segment = ((cls.get('segment') or {}).get('name') or '').lower()
        genre = ((cls.get('genre') or {}).get('name') or '').lower()
    except (AttributeError, IndexError):
        genre = ''
    if segment == 'music':
        return 'music'
    if segment == 'sports':
        return 'sports'
    if 'festival' in genre or 'festival' in (event.get('name') or '').lower():
        return 'festival'
    if 'fair' in genre or 'market' in genre:
        return 'fair'
    return 'other'


def _start(event):
    start = (event.get('dates') or {}).get('start') or {}
    if start.get('dateTime'):
        try:
            return datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
        except ValueError:
            pass
    if start.get('localDate'):
        try:
            day = datetime.strptime(start['localDate'], '%Y-%m-%d').date()
            clock = datetime.strptime(start['localTime'], '%H:%M:%S').time() if start.get('localTime') else time(0, 0)
            return timezone.make_aware(datetime.combine(day, clock))
        except ValueError:
            pass
    return None


def normalise(name):
    return re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).strip()


def parse_events(payload):
    """Turn a Discovery response into plain dicts (skipping rows without an id, name or start)."""
    items = ((payload or {}).get('_embedded') or {}).get('events') or []
    out = []
    for e in items:
        start = _start(e)
        if not (e.get('id') and e.get('name') and start):
            continue
        venue = (((e.get('_embedded') or {}).get('venues')) or [{}])[0]
        city = (venue.get('city') or {}).get('name', '')
        address = ', '.join(x for x in [(venue.get('address') or {}).get('line1', ''), city] if x)
        try:
            distance = Decimal(str(round(float(e['distance']), 1))) if e.get('distance') is not None else None
        except (TypeError, ValueError):
            distance = None
        out.append({'external_id': str(e['id'])[:100], 'name': e['name'][:200], 'category': _category(e),
                    'venue': (venue.get('name') or '')[:200], 'address': address[:255], 'start_at': start,
                    'url': (e.get('url') or '')[:200], 'distance_km': distance})
    return out


def discover(restaurant, *, days=LOOKAHEAD_DAYS, now=None):
    """Call the provider and STORE results. Returns (created, updated, skipped_duplicates).

    Raises ProviderError for any provider problem (missing key, rate limit, network).
    """
    if not settings.EVENTS_ENABLED:
        raise ProviderError('No event provider is configured (set TICKETMASTER_API_KEY). Add events by hand instead.')
    if not restaurant.has_location:
        raise ProviderError('Set the restaurant location in Settings first - events are searched around it.')
    now = now or timezone.now()
    end = now + timedelta(days=days)
    stamp = lambda d: d.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = get_json(URL, {
        'apikey': settings.TICKETMASTER_API_KEY, 'latlong': f'{restaurant.latitude},{restaurant.longitude}',
        'radius': restaurant.event_radius_km, 'unit': 'km', 'startDateTime': stamp(now), 'endDateTime': stamp(end),
        'size': 50, 'sort': 'date,asc'}, provider=PROVIDER)
    created = updated = skipped = 0
    for ev in parse_events(payload):
        existing = LocalEvent.objects.filter(provider=PROVIDER, external_id=ev['external_id']).first()
        if existing:
            for field in ('name', 'category', 'venue', 'address', 'start_at', 'url', 'distance_km'):
                setattr(existing, field, ev[field])
            existing.fetched_at = now
            existing.save()                       # keeps is_active, so a deactivated event stays deactivated
            updated += 1
            continue
        # The same real-world event is sometimes listed twice under different ids.
        local_day = timezone.localtime(ev['start_at']).date()
        twin = [e for e in LocalEvent.objects.filter(source='auto', start_at__date=local_day)
                if normalise(e.name) == normalise(ev['name']) and normalise(e.venue) == normalise(ev['venue'])]
        if twin:
            skipped += 1
            continue
        LocalEvent.objects.create(source='auto', provider=PROVIDER, fetched_at=now, **ev)
        created += 1
    cache.set(CACHE_KEY, now, 86400)
    return created, updated, skipped


def refresh(restaurant, *, force=False, now=None):
    """Cache-aware discover(): returns a one-line message. Raises ProviderError on provider problems."""
    now = now or timezone.now()
    last = cache.get(CACHE_KEY)
    if last and (now - last < timedelta(minutes=5) or (not force and now - last < REFRESH_TTL)):
        return 'Events were checked recently.'
    created, updated, skipped = discover(restaurant, now=now)
    if not (created or updated):
        return (f'{PROVIDER} returned no matching events within {restaurant.event_radius_km} km in the next '
                f'{LOOKAHEAD_DAYS} days. That only describes this provider - other local events may exist, so add them manually.')
    return f'Found {created} new and {updated} existing event(s) from {PROVIDER}' + (f' ({skipped} duplicate(s) ignored).' if skipped else '.')


def ensure_fresh(restaurant):
    """Page-load helper for automatic mode. Never raises; returns an error message or ''."""
    if restaurant.events_mode != RestaurantSettings.MODE_AUTO or not settings.EVENTS_ENABLED or not restaurant.has_location:
        return ''
    try:
        refresh(restaurant)
    except ProviderError as e:
        return str(e)
    return ''


def for_date(day, restaurant=None):
    """Active events on a date that may inform a forecast, honouring the chosen mode.

    Manual events always count. Automatic ones count only while events are in automatic mode.
    """
    restaurant = restaurant or RestaurantSettings.load()
    qs = LocalEvent.objects.filter(is_active=True, start_at__date__lte=day)
    out = []
    for e in qs:
        start_day = timezone.localtime(e.start_at).date()
        end_day = timezone.localtime(e.end_at).date() if e.end_at else start_day
        if not (start_day <= day <= end_day):
            continue
        if e.source == 'auto' and restaurant.events_mode != RestaurantSettings.MODE_AUTO:
            continue
        out.append(e)
    return out
