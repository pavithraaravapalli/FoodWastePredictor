"""Traffic readings: manual entries, an optional live API, and per-day summaries."""
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from ..models import TrafficObservation

SEVERITY = {'free': 0, 'moderate': 1, 'heavy': 2, 'standstill': 3}
FLOW_URL = 'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json'


# TomTom "functional road class": a road-type hint, not a road name (the flow API does not return names).
ROAD_CLASSES = {
    'FRC0': 'Motorway / freeway', 'FRC1': 'Major road', 'FRC2': 'Other major road', 'FRC3': 'Secondary road',
    'FRC4': 'Local connecting road', 'FRC5': 'Local road (high importance)', 'FRC6': 'Local road',
}
STALE_AFTER_HOURS = 3


class TrafficError(Exception):
    """A readable reason why a live reading could not be fetched."""


def classify(current_speed, free_flow_speed):
    """Map current vs free-flow speed to a condition."""
    if not free_flow_speed:
        raise TrafficError('The provider returned no free-flow speed for this location.')
    ratio = float(current_speed) / float(free_flow_speed)
    if ratio >= 0.8:
        return 'free'
    if ratio >= 0.5:
        return 'moderate'
    if ratio >= 0.25:
        return 'heavy'
    return 'standstill'


def fetch_live(lat, lng, api_key=None, opener=None):
    """Fetch one reading near (lat, lng). Raises TrafficError with a readable message."""
    opener = opener or urllib.request.urlopen   # looked up at call time so tests can mock it
    key = api_key if api_key is not None else settings.TRAFFIC_API_KEY
    if not key:
        raise TrafficError('No traffic API key is configured.')
    query = urllib.parse.urlencode({'point': f'{lat},{lng}', 'key': key})
    try:
        with opener(f'{FLOW_URL}?{query}', timeout=8) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise TrafficError('The traffic provider rejected the request (check the API key).' if e.code in (401, 403)
                           else f'The traffic provider returned an error ({e.code}).') from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise TrafficError('Could not reach the traffic provider. Check the internet connection and try again.') from e
    seg = data.get('flowSegmentData') if isinstance(data, dict) else None
    if not seg or 'currentSpeed' not in seg:
        raise TrafficError('The provider has no traffic data for this location.')
    condition = classify(seg['currentSpeed'], seg.get('freeFlowSpeed'))
    ratio = float(seg['currentSpeed']) / float(seg['freeFlowSpeed'])
    delay = None
    if seg.get('currentTravelTime') is not None and seg.get('freeFlowTravelTime') is not None:
        delay = max(0, int(seg['currentTravelTime']) - int(seg['freeFlowTravelTime']))
    return {'current_speed': Decimal(str(seg['currentSpeed'])),
            'free_flow_speed': Decimal(str(seg.get('freeFlowSpeed', 0))),
            'condition': condition,
            'congestion_pct': max(0, min(100, round((1 - ratio) * 100))),   # 0 = free flow, 100 = stopped
            'delay_seconds': delay,
            'road_class': ROAD_CLASSES.get(str(seg.get('frc', '')), ''),
            'confidence': seg.get('confidence')}


def daily_conditions(since=None):
    """{date: condition}: the latest observation of each (local) day."""
    qs = TrafficObservation.objects.order_by('observed_at')
    if since:
        qs = qs.filter(observed_at__date__gte=since)
    out = OrderedDict()
    for o in qs:
        out[timezone.localtime(o.observed_at).date()] = o.condition      # later rows overwrite earlier ones
    return out


REFRESH_TTL_MINUTES = 30          # automatic mode reuses a stored reading younger than this (cache + rate limit)
MIN_GAP_MINUTES = 2               # even a forced refresh waits this long between provider calls


def refresh(restaurant, *, user=None, force=False, now=None):
    """Fetch and store one automatic reading. Returns (observation_or_None, message).

    Raises TrafficError for provider problems so callers can show them without breaking the page.
    """
    from datetime import timedelta
    now = now or timezone.now()
    last = TrafficObservation.objects.filter(is_automatic=True).order_by('-fetched_at', '-observed_at').first()
    if last:
        age = now - (last.fetched_at or last.observed_at)
        if age < timedelta(minutes=MIN_GAP_MINUTES) or (not force and age < timedelta(minutes=REFRESH_TTL_MINUTES)):
            return None, 'Traffic is already up to date.'
    reading = fetch_live(restaurant.latitude, restaurant.longitude)
    obs = TrafficObservation.objects.create(
        observed_at=now, fetched_at=now, condition=reading['condition'], source=settings.TRAFFIC_PROVIDER_NAME,
        is_automatic=True, current_speed=reading['current_speed'], free_flow_speed=reading['free_flow_speed'],
        congestion_pct=reading.get('congestion_pct'), delay_seconds=reading.get('delay_seconds'),
        road_class=reading.get('road_class', ''), latitude=restaurant.latitude, longitude=restaurant.longitude,
        created_by=user)
    return obs, 'Live traffic reading saved.'


def ensure_fresh(restaurant):
    """Page-load helper for automatic mode. Never raises; returns an error message or ''."""
    if restaurant.traffic_mode != 'auto' or not settings.TRAFFIC_ENABLED or not restaurant.has_location:
        return ''
    try:
        refresh(restaurant)
    except TrafficError as e:
        return str(e)
    return ''
