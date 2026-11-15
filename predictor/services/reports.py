"""Daily / weekly / monthly reports, forecast-vs-actual, and CSV export."""
import calendar
import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from ..models import FoodRecord, Prediction
from .analytics import _group, _rows, _sum
from .records import parse_date

KINDS = ('day', 'week', 'month')
CSV_TYPES = ('summary', 'records', 'forecast')


def parse_month(value):
    try:
        y, m = value.split('-')
        return date(int(y), int(m), 1)
    except (ValueError, AttributeError):
        return None


def period_bounds(kind, params, today):
    """Return (kind, start, end, label, prev_param, next_param)."""
    kind = kind if kind in KINDS else 'week'
    if kind == 'month':
        anchor = parse_month(params.get('month')) or today.replace(day=1)
        start = anchor.replace(day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        label = start.strftime('%B %Y')
        prev, nxt = (start - timedelta(days=1)).replace(day=1), end + timedelta(days=1)
        return kind, start, end, label, f'month={prev:%Y-%m}', f'month={nxt:%Y-%m}'
    anchor = parse_date(params.get('date')) or today
    if kind == 'day':
        label = f'{anchor:%A, %d-%m-%Y}'
        return kind, anchor, anchor, label, f'date={anchor - timedelta(days=1)}', f'date={anchor + timedelta(days=1)}'
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    label = f'{start:%d-%m-%Y} – {end:%d-%m-%Y}'
    return kind, start, end, label, f'date={start - timedelta(days=7)}', f'date={start + timedelta(days=7)}'


def forecast_vs_actual(start, end):
    """Saved plan lines that also have an actual record for the same item and day."""
    preds = Prediction.objects.filter(date__range=(start, end)).select_related('food_item')
    actual = {(r['food_item_id'], r['date']): r for r in
              FoodRecord.objects.counted().filter(date__range=(start, end)).values('food_item_id', 'date', 'sold_quantity', 'prepared_quantity')}
    rows = []
    for p in preds:
        a = actual.get((p.food_item_id, p.date))
        if a is None:
            continue
        err = a['sold_quantity'] - p.predicted_demand
        rows.append({
            'date': p.date, 'item': p.food_item.name, 'predicted': p.predicted_demand,
            'recommended': p.recommended_preparation, 'planned': p.quantity_to_prepare,
            'actual_prepared': a['prepared_quantity'], 'actual': a['sold_quantity'], 'error': err,
            'error_pct': round(abs(err) / a['sold_quantity'] * 100, 1) if a['sold_quantity'] else None,
        })
    rows.sort(key=lambda r: (r['date'], r['item']))
    summary = None
    if rows:
        total_actual = sum(r['actual'] for r in rows)
        summary = {'n': len(rows), 'mae': round(sum(abs(r['error']) for r in rows) / len(rows), 1),
                   'pct': round(sum(abs(r['error']) for r in rows) / total_actual * 100, 1) if total_actual else None,
                   'within10': sum(1 for r in rows if r['error_pct'] is not None and r['error_pct'] <= 10)}
    return rows, summary


def build_report(kind, params, today):
    kind, start, end, label, prev_q, next_q = period_bounds(kind, params, today)
    qs = FoodRecord.objects.counted().filter(date__range=(start, end))
    rows = _rows(qs)
    anchor_q = f'month={start:%Y-%m}' if kind == 'month' else f'date={start}'
    ctx = {'kind': kind, 'start': start, 'end': end, 'label': label, 'prev_q': prev_q, 'next_q': next_q, 'anchor_q': anchor_q,
           'month_value': f'{start:%Y-%m}', 'date_value': start.isoformat(),
           'has_records': bool(rows), 'is_current': start <= today <= end}
    fva, fva_summary = forecast_vs_actual(start, end)
    ctx['fva'], ctx['fva_summary'] = fva, fva_summary
    if not rows:
        return ctx
    ctx['totals'] = _sum(rows)
    items = []
    for name, rs in _group(rows, 'item').items():
        items.append({'name': name, 'category': rs[0]['category'], **_sum(rs)})
    ctx['items'] = sorted(items, key=lambda i: i['sold'], reverse=True)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r['date']].append(r)
    days = [{'date': d, **_sum(rs)} for d, rs in sorted(by_day.items())]
    ctx['days'] = days
    ctx['chart'] = {'labels': [d['date'].isoformat() for d in days],
                    'prepared': [d['prepared'] for d in days], 'sold': [d['sold'] for d in days]}
    wasted = [i for i in ctx['items'] if i['waste'] > 0]
    ctx['top_waste'] = sorted(wasted, key=lambda i: i['cost'], reverse=True)[:3]
    return ctx


# ---- CSV -------------------------------------------------------------------

def safe_cell(value):
    """Stop spreadsheet formula injection: prefix cells that start like a formula."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def write_csv(response, csv_type, start, end):
    w = csv.writer(response)
    out = lambda *cells: w.writerow([safe_cell(c) for c in cells])
    rows = _rows(FoodRecord.objects.counted().filter(date__range=(start, end)))
    if csv_type == 'records':
        out('Date', 'Item', 'Category', 'Prepared', 'Sold', 'Wasted', 'Waste %', 'Waste cost')
        for r in sorted(rows, key=lambda r: (r['date'], r['item'])):
            out(r['date'].isoformat(), r['item'], r['category'], r['prepared'], r['sold'], r['waste'],
                _pct(r['waste'], r['prepared']), f"{r['cost']:.2f}")
    elif csv_type == 'forecast':
        fva, _ = forecast_vs_actual(start, end)
        out('Date', 'Item', 'Predicted demand', 'Recommended to prepare', 'Planned to prepare',
            'Actually prepared', 'Actually sold', 'Error (sold - predicted)', 'Error %')
        for r in fva:
            out(r['date'].isoformat(), r['item'], r['predicted'], r['recommended'], r['planned'],
                r['actual_prepared'], r['actual'], r['error'], r['error_pct'] if r['error_pct'] is not None else '')
    else:
        out('Item', 'Category', 'Prepared', 'Sold', 'Wasted', 'Waste %', 'Waste cost')
        for name, rs in sorted(_group(rows, 'item').items()):
            t = _sum(rs)
            out(name, rs[0]['category'], t['prepared'], t['sold'], t['waste'], t['waste_pct'], f"{t['cost']:.2f}")
        t = _sum(rows)
        out('TOTAL', '', t['prepared'], t['sold'], t['waste'], t['waste_pct'], f"{t['cost']:.2f}")


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else ''
