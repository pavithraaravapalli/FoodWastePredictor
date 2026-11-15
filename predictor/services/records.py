"""Queries and per-row maths for the inventory and daily-records pages."""
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum

from ..models import FoodItem, FoodRecord


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def filter_items(params):
    status = params.get('status', 'active')
    qs = FoodItem.objects.annotate(record_count=Count('foodrecord'))
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    else:
        status = 'all'
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q))
    category = (params.get('category') or '').strip()
    if category:
        qs = qs.filter(category__iexact=category)
    return qs, {'q': q, 'category': category, 'status': status}


def filter_records(params, only_user=None):
    """`only_user` limits the rows to that user's own records (used for Staff)."""
    qs = FoodRecord.objects.select_related('food_item', 'created_by', 'approved_by')
    if only_user is not None:
        qs = qs.filter(created_by=only_user)
    status = params.get('status') or ''
    if status in dict(FoodRecord.STATUS_CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ''
    start, end = parse_date(params.get('start')), parse_date(params.get('end'))
    if start and end and start > end:
        start, end = end, start
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    item = params.get('item') or ''
    if item.isdigit():
        qs = qs.filter(food_item_id=int(item))
    else:
        item = ''
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(food_item__name__icontains=q) | Q(event_name__icontains=q) | Q(weather__icontains=q))
    return qs.order_by('-date', '-id'), {
        'start': start.isoformat() if start else '', 'end': end.isoformat() if end else '',
        'item': item, 'q': q, 'status': status}


def records_summary(qs):
    """Totals across ALL filtered rows (not just the current page)."""
    prepared = sold = 0
    cost = Decimal('0')
    for r in qs.order_by().values('food_item__cost_per_portion').annotate(p=Sum('prepared_quantity'), s=Sum('sold_quantity')):
        prepared += r['p'] or 0
        sold += r['s'] or 0
        cost += Decimal((r['p'] or 0) - (r['s'] or 0)) * r['food_item__cost_per_portion']
    waste = prepared - sold
    return {'prepared': prepared, 'sold': sold, 'waste': waste, 'cost': cost,
            'waste_pct': round(waste / prepared * 100, 1) if prepared else None}
