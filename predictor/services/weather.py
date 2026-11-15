"""Weather: automatic (Open-Meteo) or manual entries, stored as an append-only history.

Open-Meteo (https://open-meteo.com) needs no API key for non-commercial use; commercial use needs a
paid plan - set OPEN_METEO_API_KEY to use their customer endpoint. Prediction never depends on this
module succeeding: every function degrades to "no weather available".
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from ..models import RestaurantSettings, WeatherObservation
from .http import ProviderError, get_json

PROVIDER = 'Open-Meteo'
FREE_URL = 'https://api.open-meteo.com/v1/forecast'
PAID_URL = 'https://customer-api.open-meteo.com/v1/forecast'

CURRENT_TTL = timedelta(minutes=30)       # no new automatic reading inside this window (cache + rate limit)
CURRENT_STALE = timedelta(hours=3)
FORECAST_STALE = timedelta(hours=12)
FORECAST_DAYS = 7


def condition_for_code(code):
    """WMO weather code -> one of our simple conditions."""
    if code is None:
        return 'other'
    if code in (0, 1):
        return 'sunny'
    if code in (2, 3, 45, 48):
        return 'cloudy'
    if code in (95, 96, 99):
        return 'stormy'
    if 51 <= code <= 67 or 80 <= code <= 82:
        return 'rainy'
    return 'other'                          # snow, etc.


def _dec(value, places=1):
    return None if value is None else round(Decimal(str(value)), places)


def fetch_openmeteo(lat, lng, *, days=FORECAST_DAYS):
    """Return the parsed provider payload or raise ProviderError."""
    params = {
        'latitude': lat, 'longitude': lng, 'timezone': settings.TIME_ZONE, 'forecast_days': days,
        'current': 'temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max',
    }
    url = FREE_URL
    key = getattr(settings, 'OPEN_METEO_API_KEY', '')
    if key:
        url, params['apikey'] = PAID_URL, key
    data = get_json(url, params, provider=PROVIDER)
    if not isinstance(data, dict) or 'current' not in data or 'daily' not in data:
        raise ProviderError(f'{PROVIDER} returned no weather for this location.')
    return data


def refresh(restaurant=None, *, force=False, now=None):
    """Fetch and STORE a current reading and the daily forecast. Returns (rows_added, message).

    Skips the call when a recent automatic reading exists (cache) unless forced - but even a forced
    refresh is refused inside 5 minutes to respect the provider's rate limits.
    """
    restaurant = restaurant or RestaurantSettings.load()
    now = now or timezone.now()
    if not restaurant.has_location:
        raise ProviderError('Set the restaurant location in Settings first - weather is fetched for those coordinates.')
    last = (WeatherObservation.objects.filter(source='auto', kind='current').order_by('-fetched_at').first())
    if last and last.fetched_at:
        age = now - last.fetched_at
        if age < timedelta(minutes=5) or (not force and age < CURRENT_TTL):
            return 0, 'Weather is already up to date.'
    data = fetch_openmeteo(restaurant.latitude, restaurant.longitude)
    cur, daily = data['current'], data['daily']
    today = timezone.localdate(now)
    added = 0
    code = cur.get('weather_code')
    WeatherObservation.objects.create(
        kind='current', source='auto', provider=PROVIDER, target_date=today, observed_at=now, fetched_at=now,
        condition=condition_for_code(code), weather_code=code, temperature_c=_dec(cur.get('temperature_2m')),
        precipitation_mm=_dec(cur.get('precipitation')), humidity_pct=cur.get('relative_humidity_2m'),
        wind_kmh=_dec(cur.get('wind_speed_10m')))
    added += 1
    dates = daily.get('time') or []
    for i, day in enumerate(dates):
        def col(name):
            values = daily.get(name) or []
            return values[i] if i < len(values) else None
        code = col('weather_code')
        WeatherObservation.objects.create(
            kind='forecast', source='auto', provider=PROVIDER, target_date=day, observed_at=now, fetched_at=now,
            condition=condition_for_code(code), weather_code=code, temp_min_c=_dec(col('temperature_2m_min')),
            temp_max_c=_dec(col('temperature_2m_max')), precipitation_mm=_dec(col('precipitation_sum')),
            precipitation_probability=col('precipitation_probability_max'))
        added += 1
    return added, f'Fetched current weather and a {len(dates)}-day forecast from {PROVIDER}.'


def ensure_fresh(restaurant=None):
    """Called when a page is shown in automatic mode. Never raises; returns an error message or ''."""
    restaurant = restaurant or RestaurantSettings.load()
    if restaurant.weather_mode != RestaurantSettings.MODE_AUTO:
        return ''
    try:
        refresh(restaurant)
    except ProviderError as e:
        return str(e)
    return ''


def latest_current():
    return WeatherObservation.objects.filter(kind='current').order_by('-observed_at', '-id').first()


def for_date(day, restaurant=None):
    """The best available weather for a date, honouring the chosen mode.

    Automatic mode -> latest automatic forecast row for that day (or the latest reading if it is today).
    Manual mode    -> latest manual entry for that day.
    A manual entry for the day always wins over an automatic one, because a person looked out of the window.
    Returns None when nothing is available; callers must cope with that.
    """
    restaurant = restaurant or RestaurantSettings.load()
    qs = WeatherObservation.objects.filter(target_date=day)
    manual = qs.filter(source='manual').order_by('-observed_at', '-id').first()
    if manual:
        return manual
    if restaurant.weather_mode == RestaurantSettings.MODE_AUTO:
        return qs.filter(source='auto', kind='forecast').order_by('-fetched_at', '-id').first() or \
            qs.filter(source='auto', kind='current').order_by('-fetched_at', '-id').first()
    return None


def forecast_rows(start, days=FORECAST_DAYS):
    """Latest stored automatic forecast for each upcoming day, oldest day first."""
    rows = {}
    for o in (WeatherObservation.objects.filter(kind='forecast', source='auto', target_date__gte=start,
                                                target_date__lt=start + timedelta(days=days))
              .order_by('target_date', '-fetched_at', '-id')):
        rows.setdefault(o.target_date, o)
    return [rows[d] for d in sorted(rows)]


def status(obs, now=None):
    """('ok' | 'stale' | 'none', message) for the freshness of a stored automatic reading."""
    now = now or timezone.now()
    if obs is None:
        return 'none', 'No weather has been recorded yet.'
    if obs.source == 'manual':
        return 'ok', ''
    limit = CURRENT_STALE if obs.kind == 'current' else FORECAST_STALE
    when = obs.fetched_at or obs.observed_at
    if now - when > limit:
        return 'stale', f'This reading is outdated (last updated {timezone.localtime(when):%d-%m-%Y %H:%M}).'
    return 'ok', ''
