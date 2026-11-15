"""Waste analytics: everything is computed from FoodRecord rows in the chosen range."""
import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from ..models import FoodRecord, RestaurantSettings
from .dashboard import _delta, _pct, _totals, resolve_range

MIN_RECORDS_FOR_ITEM_ADVICE = 7
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _rows(qs):
    """Plain dicts (one per record) with waste and cost worked out."""
    out = []
    for r in qs.values('date', 'prepared_quantity', 'sold_quantity', 'food_item_id', 'food_item__name',
                       'food_item__category', 'food_item__cost_per_portion'):
        waste = r['prepared_quantity'] - r['sold_quantity']
        out.append({
            'date': r['date'], 'prepared': r['prepared_quantity'], 'sold': r['sold_quantity'], 'waste': waste,
            'item_id': r['food_item_id'], 'item': r['food_item__name'], 'category': r['food_item__category'],
            'cost': Decimal(waste) * r['food_item__cost_per_portion'],
            'unit_cost': r['food_item__cost_per_portion'],
        })
    return out


def _sum(rows):
    prepared = sum(r['prepared'] for r in rows)
    sold = sum(r['sold'] for r in rows)
    waste = prepared - sold
    return {'prepared': prepared, 'sold': sold, 'waste': waste, 'waste_pct': _pct(waste, prepared),
            'cost': sum((r['cost'] for r in rows), Decimal('0')), 'days': len({r['date'] for r in rows})}


def _bucket_trend(rows, first, last):
    """Daily buckets for short ranges, Monday-start weekly buckets for long ones."""
    weekly = (last - first).days > 45
    buckets = defaultdict(lambda: {'prepared': 0, 'sold': 0, 'cost': Decimal('0')})
    for r in rows:
        key = r['date'] - timedelta(days=r['date'].weekday()) if weekly else r['date']
        b = buckets[key]
        b['prepared'] += r['prepared']; b['sold'] += r['sold']; b['cost'] += r['cost']
    labels, pct, cost = [], [], []
    for key in sorted(buckets):
        b = buckets[key]
        labels.append(key.isoformat())
        pct.append(_pct(b['prepared'] - b['sold'], b['prepared']))
        cost.append(float(b['cost']))
    return {'labels': labels, 'waste_pct': pct, 'cost': cost, 'unit': 'week' if weekly else 'day'}


def _group(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return groups


def _period_compare(all_rows_fn, today, days):
    """Last `days` days vs the `days` before them, only if coverage is comparable."""
    cur_start = today - timedelta(days=days - 1)
    prev_start = cur_start - timedelta(days=days)
    cur = _sum(all_rows_fn(cur_start, today))
    prev = _sum(all_rows_fn(prev_start, cur_start - timedelta(days=1)))
    if not cur['days'] or not prev['days'] or prev['days'] < cur['days'] * 0.5:
        return None
    return {'cur': cur, 'prev': prev,
            'pct_delta': _delta(cur['waste_pct'], prev['waste_pct'], points=True),
            'cost_delta': _delta(float(cur['cost']), float(prev['cost']))}


def _percentile(values, q):
    values = sorted(values)
    if not values:
        return 0
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _item_advice(items_rows, currency, threshold):
    """Data-backed preparation suggestion per item, with the trade-off spelled out."""
    advice = []
    for name, rows in items_rows.items():
        if len(rows) < MIN_RECORDS_FOR_ITEM_ADVICE:
            continue
        totals = _sum(rows)
        if (totals['waste_pct'] or 0) < max(10, threshold / 2):
            continue
        target = math.ceil(_percentile([r['sold'] for r in rows], 0.8))
        avg_prep = totals['prepared'] / len(rows)
        if target >= avg_prep - 1:
            continue
        saved_cost, lost = Decimal('0'), 0
        for r in rows:
            new_prep = min(r['prepared'], target)
            new_waste = max(new_prep - r['sold'], 0)
            saved_cost += Decimal(r['waste'] - new_waste) * r['unit_cost']
            lost += max(r['sold'] - new_prep, 0)
        busy_days = sum(1 for r in rows if r['sold'] >= target)
        if saved_cost <= 0:
            continue
        advice.append({
            'score': float(saved_cost), 'tone': 'warn' if (totals['waste_pct'] or 0) > threshold else 'neutral',
            'title': f'Prepare about {target} {name} instead of {avg_prep:.0f}',
            'body': f'You averaged {avg_prep:.0f} prepared and {totals["sold"] / len(rows):.0f} sold '
                    f'({totals["waste_pct"]}% wasted). Applying {target} to these {len(rows)} records would have '
                    f'saved about {currency}{saved_cost:,.0f} of food cost. On {busy_days} of those days you sold '
                    f'{target} or more, so you might have run out sooner'
                    + (f' (about {lost} portions fewer sold).' if lost else '.'),
        })
    advice.sort(key=lambda a: a['score'], reverse=True)
    return advice[:3]


def build_analytics(params, today):
    rest = RestaurantSettings.load()
    currency, threshold = rest.currency_symbol, rest.waste_alert_threshold
    all_qs = FoodRecord.objects.counted()
    has_any = all_qs.exists()
    start, end, range_key, notice = resolve_range(
        params, today, has_any, lambda s, e: all_qs.filter(date__range=(s, e)).exists())

    qs = all_qs.filter(date__lte=end)
    if start:
        qs = qs.filter(date__gte=start)
    ctx = {'range_key': range_key, 'range_notice': notice, 'start': start, 'end': end,
           'has_any_records': has_any, 'has_records': qs.exists(), 'currency': currency, 'threshold': threshold}
    if not ctx['has_records']:
        return ctx

    rows = _rows(qs)
    totals = _sum(rows)
    ctx['totals'] = totals

    # Previous period of equal length (bounded ranges), only with comparable coverage.
    prev = None
    if start:
        span = (end - start).days + 1
        prev_rows = _rows(all_qs.filter(date__range=(start - timedelta(days=span), start - timedelta(days=1))))
        p = _sum(prev_rows)
        if p['days'] and p['days'] >= totals['days'] * 0.5:
            prev = p
    ctx['deltas'] = {
        'waste_pct': _delta(totals['waste_pct'], prev and prev['waste_pct'], points=True),
        'waste': _delta(totals['waste'], prev and prev['waste']),
        'cost': _delta(float(totals['cost']), prev and float(prev['cost'])),
    } if prev else {}
    ctx['has_previous'] = prev is not None

    first = start or min(r['date'] for r in rows)
    ctx['trend'] = _bucket_trend(rows, first, end)

    by_item = _group(rows, 'item')
    items = []
    for name, rs in by_item.items():
        t = _sum(rs)
        items.append({'name': name, 'category': rs[0]['category'], **t})
    ctx['items_by_cost'] = sorted([i for i in items if i['waste'] > 0], key=lambda i: i['cost'], reverse=True)[:6]
    ctx['items_max'] = float(ctx['items_by_cost'][0]['cost']) if ctx['items_by_cost'] else 0

    cats = []
    for cat, rs in _group(rows, 'category').items():
        t = _sum(rs)
        cats.append({'name': cat, **t})
    cats = [c for c in cats if c['waste'] > 0]
    cats.sort(key=lambda c: c['cost'], reverse=True)
    total_cost = sum((c['cost'] for c in cats), Decimal('0'))
    for c in cats:
        c['share'] = round(float(c['cost'] / total_cost * 100)) if total_cost else 0
    ctx['categories'] = cats

    wk = defaultdict(list)
    for r in rows:
        wk[r['date'].weekday()].append(r)
    weekday = []
    for i in range(7):
        t = _sum(wk[i]) if wk[i] else None
        weekday.append({'name': WEEKDAYS[i][:3], 'full': WEEKDAYS[i],
                        'waste_pct': t['waste_pct'] if t else None, 'n': len(wk[i])})
    ctx['weekday'] = weekday

    ctx['compare'] = {
        'week': _period_compare(lambda s, e: _rows(all_qs.filter(date__range=(s, e))), today, 7),
        'month': _period_compare(lambda s, e: _rows(all_qs.filter(date__range=(s, e))), today, 30),
    }

    # ---- suggestions ----
    suggestions = _item_advice(by_item, currency, threshold)
    ranked = [w for w in weekday if w['waste_pct'] is not None and w['n'] >= 3]
    if len(ranked) >= 3 and totals['waste_pct'] is not None:
        worst = max(ranked, key=lambda w: w['waste_pct'])
        if worst['waste_pct'] >= totals['waste_pct'] + 5:
            suggestions.append({'tone': 'neutral', 'title': f'{worst["full"]}s waste the most',
                                'body': f'{worst["waste_pct"]}% of what you prepare on {worst["full"]}s goes unsold, against '
                                        f'{totals["waste_pct"]}% overall. Prepare less on {worst["full"]}s, or check the '
                                        'Demand Forecast, which averages the same weekday.'})
    if cats and cats[0]['share'] >= 40 and len(cats) > 1:
        suggestions.append({'tone': 'neutral', 'title': f'{cats[0]["name"]} is {cats[0]["share"]}% of your waste cost',
                            'body': f'About {currency}{cats[0]["cost"]:,.0f} of food cost in this period. Start there for the biggest saving.'})
    if not suggestions:
        suggestions.append({'tone': 'good', 'title': 'No clear waste problem in this period',
                            'body': 'Nothing stands out. Suggestions appear once an item has 7+ records with meaningful waste.'})
    ctx['suggestions'] = suggestions[:5]
    return ctx
