"""Demand forecasting from the restaurant's own sales history.

This is an explainable statistical estimate, NOT machine learning:

1. Baseline  - average units sold on the same weekday (last 6 such days within
               90 days). If there are fewer than 3 of those, fall back to the
               average of the last 28 days. With fewer than 3 records: no forecast.
2. Factors   - optional weather / holiday / event adjustments. Each is a plain
               ratio learned from the item's own history (average sales on days
               with that condition vs days without), used only when there are
               enough matching past days, and clamped to a sensible range.
3. Reserve   - half a standard deviation of the comparable days' sales.
4. Reliability - a back-test: how far off this same baseline method would have
               been on the item's last recorded days. Shown only with >= 5 points.

Traffic is an optional input too: the expected condition is compared with
past days that have a recorded traffic reading. Not used (and said so in the
explanation): time-of-day patterns (not recorded).
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, stdev

from ..models import FoodItem, FoodRecord, TrafficObservation

Obs = namedtuple('Obs', 'date prepared sold weather is_holiday event traffic', defaults=('',))

MIN_SAMPLE = 3                 # records needed for a baseline or a factor
WEEKDAY_SAMPLE = 6             # same-weekday days averaged
WEEKDAY_WINDOW_DAYS = 90
RECENT_WINDOW_DAYS = 28
FACTOR_CLAMP = (0.7, 1.3)
TOTAL_FACTOR_CLAMP = (0.6, 1.5)
MIN_BACKTEST_POINTS = 5
MAX_BACKTEST_POINTS = 10
HISTORY_LOAD_DAYS = 200        # how far back we read records (window + backtest)


@dataclass
class Forecast:
    item: FoodItem
    target: date
    ok: bool = False
    expected: int = 0
    reserve: int = 0
    recommended: int = 0
    method: str = ''
    method_label: str = ''
    baseline: float = 0.0
    n_history: int = 0
    sample: list = field(default_factory=list)      # [(date, sold)] used for the baseline
    factors: list = field(default_factory=list)     # dicts: label, ratio, used, note
    ratio: float = 1.0
    notes: list = field(default_factory=list)       # plain-language explanation lines
    warnings: list = field(default_factory=list)
    not_used: list = field(default_factory=list)
    reliability: dict | None = None
    recent_prepared: float | None = None
    recent_sold: float | None = None
    recent_waste_pct: float | None = None
    chart: dict | None = None
    missing: int = 0                                 # records still needed when not ok

    @property
    def method_code(self):
        return self.method

    def explanation_text(self):
        return '\n'.join(self.notes)


def load_observations(items, target):
    """{item_id: [Obs, ...]} for records strictly before `target`, oldest first."""
    since = target - timedelta(days=HISTORY_LOAD_DAYS)
    from .traffic import daily_conditions
    traffic = daily_conditions(since)
    out = {i.pk: [] for i in items}
    rows = (FoodRecord.objects.counted()
            .filter(food_item__in=list(out), date__lt=target, date__gte=since)
            .order_by('date', 'id')
            .values_list('food_item_id', 'date', 'prepared_quantity', 'sold_quantity',
                         'weather', 'is_holiday', 'event_name'))
    for item_id, d, prep, sold, weather, hol, event in rows:
        out[item_id].append(Obs(d, prep, sold, (weather or '').strip(), hol, (event or '').strip(), traffic.get(d, '')))
    return out


def baseline(obs, target):
    """Return (mean, sample_obs, method_code) or None. `obs` must all be before target."""
    window = [o for o in obs if o.date >= target - timedelta(days=WEEKDAY_WINDOW_DAYS)]
    same = sorted((o for o in window if o.date.weekday() == target.weekday()),
                  key=lambda o: o.date, reverse=True)[:WEEKDAY_SAMPLE]
    if len(same) >= MIN_SAMPLE:
        return mean(o.sold for o in same), same, 'weekday'
    recent = [o for o in obs if o.date >= target - timedelta(days=RECENT_WINDOW_DAYS)]
    if len(recent) >= MIN_SAMPLE:
        return mean(o.sold for o in recent), recent, 'recent'
    return None


def _factor(label, obs, predicate, min_match):
    """Ratio of average sales on matching days vs non-matching days (90-day pool)."""
    match = [o.sold for o in obs if predicate(o)]
    other = [o.sold for o in obs if not predicate(o)]
    if len(match) < min_match or len(other) < MIN_SAMPLE:
        return {'label': label, 'ratio': 1.0, 'used': False,
                'note': f'{label}: only {len(match)} matching past day{"s" if len(match) != 1 else ""} '
                        f'(need {min_match}+ and {MIN_SAMPLE}+ others), so no adjustment was made.'}
    raw = mean(match) / mean(other) if mean(other) else 1.0
    ratio = min(max(raw, FACTOR_CLAMP[0]), FACTOR_CLAMP[1])
    change = (ratio - 1) * 100
    word = 'higher' if change > 0.5 else 'lower' if change < -0.5 else 'about the same'
    amount = f' ({abs(change):.0f}% {word})' if word != 'about the same' else ''
    capped = ' (limited to the allowed range)' if ratio != raw else ''
    return {'label': label, 'ratio': ratio, 'used': True,
            'note': f'{label}: across {len(match)} matching past days, sales averaged '
                    f'{mean(match):.1f} vs {mean(other):.1f} on other days{amount}{capped}.'}


def _backtest(obs):
    """Back-test the baseline on the most recent recorded days. None if too few points."""
    errors, actuals = [], []
    for idx in range(len(obs) - 1, -1, -1):
        if len(errors) >= MAX_BACKTEST_POINTS:
            break
        target = obs[idx]
        b = baseline(obs[:idx], target.date)
        if b is None:
            continue
        errors.append(abs(b[0] - target.sold))
        actuals.append(target.sold)
    if len(errors) < MIN_BACKTEST_POINTS or sum(actuals) == 0:
        return None
    mae = mean(errors)
    pct = sum(errors) / sum(actuals) * 100
    level = 'High' if pct <= 10 else 'Moderate' if pct <= 20 else 'Low'
    return {'n': len(errors), 'mae': round(mae, 1), 'pct': round(pct), 'level': level}


def forecast_item(item, obs, target, *, weather='', is_holiday=False, event='', traffic=''):
    """Forecast one item for `target` using `obs` (records before target, oldest first)."""
    f = Forecast(item=item, target=target, n_history=len(obs))
    weather, event = (weather or '').strip(), (event or '').strip()
    traffic = (traffic or '').strip().lower()
    f.not_used = ['Time-of-day patterns (sales times are not recorded)']
    if not traffic:
        f.not_used.append('Traffic (no expected traffic was entered)')

    b = baseline(obs, target)
    if b is None:
        f.missing = max(MIN_SAMPLE - len(obs), 1)
        f.notes = [f'Only {len(obs)} past record{"s" if len(obs) != 1 else ""} for {item.name}. '
                   f'At least {MIN_SAMPLE} are needed for a forecast.']
        return f

    base, sample, method = b
    f.ok, f.baseline, f.method = True, base, method
    f.sample = [(o.date, o.sold) for o in sorted(sample, key=lambda o: o.date)]
    day = target.strftime('%A')
    dates = ', '.join(f'{d:%d-%m-%Y}' for d, _ in f.sample)
    if method == 'weekday':
        f.method_label = f'Average of the last {len(sample)} {day}s'
        f.notes.append(f'Baseline: you sold {base:.1f} portions on average on the last {len(sample)} {day}s ({dates}).')
    else:
        f.method_label = f'Average of the last {RECENT_WINDOW_DAYS} days'
        f.notes.append(f'Baseline: fewer than {MIN_SAMPLE} past {day}s are recorded, so this uses the average of '
                       f'{len(sample)} records from the last {RECENT_WINDOW_DAYS} days ({base:.1f} portions). '
                       f'The weekday pattern is not captured yet.')

    # Optional adjustments learned from this item's own history.
    pool = [o for o in obs if o.date >= target - timedelta(days=WEEKDAY_WINDOW_DAYS)]
    if weather:
        f.factors.append(_factor(f'Weather "{weather}"', pool, lambda o, w=weather.lower(): o.weather.lower() == w, MIN_SAMPLE))
    if is_holiday:
        f.factors.append(_factor('Holiday', pool, lambda o: o.is_holiday, MIN_SAMPLE))
    if event:
        f.factors.append(_factor(f'Event "{event}"', pool, lambda o, e=event.lower(): o.event.lower() == e, 2))
    if traffic:
        label = dict(TrafficObservation.CONDITIONS).get(traffic, traffic)
        with_data = [o for o in pool if o.traffic]
        if not with_data:
            f.factors.append({'label': f'Traffic "{label}"', 'ratio': 1.0, 'used': False,
                              'note': f'Traffic "{label}": no traffic has been recorded for past days yet, so no adjustment was made.'})
        else:
            f.factors.append(_factor(f'Traffic "{label}"', with_data, lambda o, t=traffic: o.traffic == t, MIN_SAMPLE))
    ratio = 1.0
    for fac in f.factors:
        ratio *= fac['ratio']
        f.notes.append(fac['note'])
    f.ratio = min(max(ratio, TOTAL_FACTOR_CLAMP[0]), TOTAL_FACTOR_CLAMP[1])
    if not f.factors:
        f.notes.append('No weather, holiday, event or traffic was entered, so no adjustments were applied.')

    f.expected = max(round(base * f.ratio), 0)

    # Reserve: half a standard deviation of the comparable days, scaled with the adjustment.
    sd = stdev([o.sold for o in sample]) if len(sample) > 1 else 0.0
    f.reserve = max(round(0.5 * sd * f.ratio), 0)
    f.recommended = f.expected + f.reserve
    f.notes.append(f'Safety reserve: {f.reserve} portion{"s" if f.reserve != 1 else ""} '
                   f'(half the usual day-to-day swing of {sd:.1f}).')

    # Context from recent history.
    recent = [o for o in obs if o.date >= target - timedelta(days=RECENT_WINDOW_DAYS)] or sample
    if recent:
        prep_total = sum(o.prepared for o in recent)
        f.recent_prepared = mean(o.prepared for o in recent)
        f.recent_sold = mean(o.sold for o in recent)
        f.recent_waste_pct = round((prep_total - sum(o.sold for o in recent)) / prep_total * 100, 1) if prep_total else None

    # Honest caveat: sold == prepared may hide unmet demand.
    sold_out = sum(1 for o in sample if o.prepared and o.sold >= o.prepared)
    if sold_out and sold_out / len(sample) >= 0.3:
        f.warnings.append(f'You sold out completely on {sold_out} of the {len(sample)} days used. '
                          'Real demand may have been higher than sales show, so consider a larger reserve.')

    f.reliability = _backtest(obs)
    if f.reliability is None:
        f.notes.append('Reliability is not shown yet: at least '
                       f'{MIN_BACKTEST_POINTS} past days are needed to test how accurate this method has been.')

    last = obs[-14:]
    f.chart = {
        'labels': [o.date.isoformat() for o in last] + [target.isoformat()],
        'sold': [o.sold for o in last],
        'expected': f.expected, 'recommended': f.recommended,
    }
    return f


def forecast_many(items, target, **inputs):
    """Forecast several items at once (one query for all history)."""
    items = list(items)
    history = load_observations(items, target)
    return [forecast_item(i, history[i.pk], target, **inputs) for i in items]


def save_forecasts(forecasts):
    """Upsert Prediction rows. Approved/prepared lines are left untouched.

    Returns (saved, skipped_protected, skipped_no_data).
    """
    from ..models import Prediction
    saved = protected = no_data = 0
    for f in forecasts:
        if not f.ok:
            no_data += 1
            continue
        existing = Prediction.objects.filter(food_item=f.item, date=f.target).first()
        if existing and existing.status != Prediction.STATUS_SUGGESTED:
            protected += 1
            continue
        Prediction.objects.update_or_create(
            food_item=f.item, date=f.target,
            defaults={'predicted_demand': f.expected, 'recommended_preparation': f.recommended,
                      'safety_reserve': f.reserve, 'method': f.method,
                      'explanation': f.explanation_text(), 'status': Prediction.STATUS_SUGGESTED,
                      'approved_quantity': None, 'approved_by': None, 'approved_at': None})
        saved += 1
    return saved, protected, no_data
