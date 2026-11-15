from decimal import Decimal, InvalidOperation

from django import template

from ..services import units

register = template.Library()


@register.filter
def qty(value, base_unit):
    """4300 -> '4.3 kg' for a quantity stored in g/ml/pcs."""
    try:
        return units.pretty(Decimal(value), base_unit)
    except (InvalidOperation, TypeError, ValueError):
        return '—'


@register.filter
def signed_qty(value, base_unit):
    try:
        v = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    return ('+' if v > 0 else '') + units.pretty(v, base_unit)


@register.filter
def money(value):
    try:
        return f'{Decimal(value):,.2f}'
    except (InvalidOperation, TypeError, ValueError):
        return '—'


@register.filter
def get_item(mapping, key):
    return mapping.get(key, [])
