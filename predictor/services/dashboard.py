"""Dashboard analytics. Everything here is computed from FoodRecord rows."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from ..models import FoodItem, FoodRecord, RestaurantSettings

PRESETS = {'7': 7, '30': 30, '90': 90}


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else None


def resolve_range(params, today, has_any_records, window_has_records):
    """Work out (start, end, key, notice) from the query string.

    key is '7' / '30' / '90' / 'all' / 'custom' so the template can highlight it.
    `window_has_records(start, end)` lets us fall back to all-time when the
    default 30-day window is empty but older data exists.
    """
    from datetime import date

    def parse(value):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    range_key = params.get('range')
    start, end = parse(params.get('start')), parse(params.get('end'))

    if start and end:
        if start > end:
            start, end = end, start
        return start, end, 'custom', None
    if range_key == 'all':
        return None, today, 'all', None
    if range_key in PRESETS:
        return today - timedelta(days=PRESETS[range_key] - 1), today, range_key, None

    # No explicit choice: default to 30 days, but don't greet the user with an
    # empty page if their data is simply older than that.
    start = today - timedelta(days=29)
    if has_any_records and not window_has_records(start, today):
        return None, today, 'all', 'No records in the last 30 days, so all recorded data is shown.'
    return start, today, '30', None


def _totals(qs):
    """Prepared / sold / waste / waste cost for a queryset of FoodRecord."""
    rows = qs.values('food_item__cost_per_portion').annotate(
        prepared=Sum('prepared_quantity'), sold=Sum('sold_quantity'))
    prepared = sold = 0
    cost = Decimal('0')
    for r in rows:
        prepared += r['prepared'] or 0
        sold += r['sold'] or 0
        cost += Decimal((r['prepared'] or 0) - (r['sold'] or 0)) * r['food_item__cost_per_portion']
    waste = prepared - sold
    return {
        'prepared': prepared, 'sold': sold, 'waste': waste,
        'waste_pct': _pct(waste, prepared), 'cost': cost,
    }


def _delta(current, previous, *, points=False):
    """Change vs the previous period. None when there is nothing to compare."""
    if current is None or previous is None:
        return None
    if points:
        diff = round(current - previous, 1)
        return {'value': abs(diff), 'unit': ' pts', 'dir': 'up' if diff > 0 else 'down' if diff < 0 else 'flat'}
    if previous == 0:
        return None
    diff = (current - previous) / previous * 100
    return {'value': abs(round(diff, 1)), 'unit': '%', 'dir': 'up' if diff > 0 else 'down' if diff < 0 else 'flat'}


def build_dashboard(params, today):
    rest = RestaurantSettings.load()
    threshold, currency = rest.waste_alert_threshold, rest.currency_symbol
    all_records = FoodRecord.objects.counted()
    has_any = all_records.exists()

    start, end, range_key, notice = resolve_range(
        params, today, has_any,
        lambda s, e: all_records.filter(date__range=(s, e)).exists())

    qs = all_records.filter(date__lte=end)
    if start:
        qs = qs.filter(date__gte=start)

    ctx = {
        'has_any_records': has_any,
        'has_food_items': FoodItem.objects.exists(),
        'range_key': range_key,
        'range_notice': notice,
        'start': start, 'end': end,
        'has_records': qs.exists(),
        'threshold': threshold,
    }
    if not ctx['has_records']:
        ctx['alerts'] = _alerts(all_records, today, threshold, [], has_any)
        return ctx

    totals = _totals(qs)
    ctx['totals'] = totals

    # Previous period of equal length (only for bounded ranges).
    prev = None
    if start:
        span = (end - start).days + 1
        prev_qs = all_records.filter(date__range=(start - timedelta(days=span), start - timedelta(days=1)))
        # Only compare when the earlier period has comparable coverage;
        # otherwise a 4-day history vs a 30-day window looks like a "500% jump".
        current_days = qs.values('date').distinct().count()
        prev_days = prev_qs.values('date').distinct().count()
        if prev_days and prev_days >= current_days * 0.5:
            prev = _totals(prev_qs)
    ctx['has_previous'] = prev is not None
    ctx['deltas'] = {
        'prepared': _delta(totals['prepared'], prev and prev['prepared']),
        'sold': _delta(totals['sold'], prev and prev['sold']),
        'waste_pct': _delta(totals['waste_pct'], prev and prev['waste_pct'], points=True),
        'cost': _delta(float(totals['cost']), prev and float(prev['cost'])),
    } if prev else {}

    # Per-item aggregates
    items = []
    for r in qs.values('food_item__name', 'food_item__category', 'food_item__cost_per_portion').annotate(
            prepared=Sum('prepared_quantity'), sold=Sum('sold_quantity')):
        waste = r['prepared'] - r['sold']
        items.append({
            'name': r['food_item__name'], 'category': r['food_item__category'],
            'prepared': r['prepared'], 'sold': r['sold'], 'waste': waste,
            'waste_pct': _pct(waste, r['prepared']),
            'cost': Decimal(waste) * r['food_item__cost_per_portion'],
        })
    top_sold = sorted(items, key=lambda i: i['sold'], reverse=True)[:5]
    top_wasted = [i for i in sorted(items, key=lambda i: (i['waste'], i['cost']), reverse=True) if i['waste'] > 0][:5]
    ctx['top_sold'] = top_sold
    ctx['top_sold_max'] = top_sold[0]['sold'] if top_sold else 0
    ctx['top_wasted'] = top_wasted
    ctx['top_wasted_max'] = top_wasted[0]['waste'] if top_wasted else 0

    # Daily series for the chart
    daily = defaultdict(lambda: [0, 0])
    for r in qs.values('date').annotate(prepared=Sum('prepared_quantity'), sold=Sum('sold_quantity')):
        daily[r['date']] = [r['prepared'], r['sold']]
    first = start or min(daily)
    days = (end - first).days + 1
    chart = {'labels': [], 'sold': [], 'wasted': [], 'prepared': []}
    for i in range(days):
        d = first + timedelta(days=i)
        prepared, sold = daily.get(d, [0, 0])
        chart['labels'].append(d.isoformat())
        chart['sold'].append(sold)
        chart['wasted'].append(prepared - sold)
        chart['prepared'].append(prepared)
    ctx['chart'] = chart

    ctx['recent'] = list(qs.select_related('food_item').order_by('-date', '-id')[:6])
    ctx['insight'] = _insight(totals, prev, top_wasted, currency, threshold)
    ctx['alerts'] = _alerts(all_records, today, threshold, items, has_any)
    return ctx


def _insight(totals, prev, top_wasted, cur, threshold):
    """One plain-language observation, derived only from the data shown."""
    pct = totals['waste_pct']
    if prev and prev['waste_pct'] is not None and pct is not None and abs(pct - prev['waste_pct']) >= 1:
        diff = round(abs(pct - prev['waste_pct']), 1)
        better = pct < prev['waste_pct']
        return {
            'tone': 'good' if better else 'warn',
            'title': f"Waste rate {'fell' if better else 'rose'} {diff} points vs the previous period",
            'body': f"{pct}% of prepared food went unsold, compared with {prev['waste_pct']}% before."
                    + (f" {top_wasted[0]['name']} accounts for the most waste." if top_wasted else ''),
        }
    if top_wasted:
        w = top_wasted[0]
        share = _pct(w['waste'], totals['waste'])
        return {
            'tone': 'warn' if (w['waste_pct'] or 0) > threshold else 'neutral',
            'title': f"{w['name']} is your biggest source of waste",
            'body': f"{w['waste']} unsold portions ({w['waste_pct']}% of what was prepared), "
                    f"about {cur}{w['cost']:,.0f} in food cost — {share}% of all waste in this period.",
        }
    return {
        'tone': 'good', 'title': 'No unsold food in this period',
        'body': 'Everything prepared was sold. Keep logging records to spot patterns over time.',
    }


def _alerts(all_records, today, threshold, items, has_any):
    alerts = []
    if has_any and not all_records.filter(date=today).exists():
        alerts.append({'level': 'info', 'icon': 'clipboard-list',
                       'text': "No records logged for today yet.", 'action': 'add_record', 'action_label': 'Add record'})
    for i in sorted(items, key=lambda i: i['waste_pct'] or 0, reverse=True):
        if (i['waste_pct'] or 0) > threshold and i['prepared'] >= 10:
            alerts.append({'level': 'warn', 'icon': 'triangle-alert',
                           'text': f"{i['name']} waste is {i['waste_pct']}% (above the {threshold}% limit)."})
    return alerts[:4]
