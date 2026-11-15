"""Safe unit handling for recipes and stock.

Stock is always stored in a BASE unit: grams (g), millilitres (ml) or pieces (pcs).
Mass, volume and count can never be mixed - converting between them raises UnitError
(a gram of oil is not a millilitre of oil, and 'pieces' has no weight).
"""
from decimal import Decimal

UNITS = {            # unit -> (dimension, factor to the base unit)
    'g': ('mass', Decimal('1')), 'kg': ('mass', Decimal('1000')),
    'ml': ('volume', Decimal('1')), 'l': ('volume', Decimal('1000')),
    'pcs': ('count', Decimal('1')),
}
BASE_OF = {'mass': 'g', 'volume': 'ml', 'count': 'pcs'}
UNIT_CHOICES = [('g', 'Grams (g)'), ('kg', 'Kilograms (kg)'), ('ml', 'Millilitres (ml)'),
                ('l', 'Litres (l)'), ('pcs', 'Pieces')]
BASE_CHOICES = [('g', 'Grams (g)'), ('ml', 'Millilitres (ml)'), ('pcs', 'Pieces')]


class UnitError(ValueError):
    pass


def dimension(unit):
    try:
        return UNITS[unit][0]
    except KeyError:
        raise UnitError(f'Unknown unit "{unit}".') from None


def compatible(a, b):
    return dimension(a) == dimension(b)


def convert(quantity, from_unit, to_unit):
    """Convert `quantity` between two units of the SAME dimension, otherwise raise UnitError."""
    if not compatible(from_unit, to_unit):
        raise UnitError(f'Cannot convert {from_unit} to {to_unit}: they measure different things.')
    return Decimal(quantity) * UNITS[from_unit][1] / UNITS[to_unit][1]


def to_base(quantity, unit):
    return convert(quantity, unit, BASE_OF[dimension(unit)])


def pretty(quantity, base_unit):
    """Human friendly quantity: 4300 g -> '4.3 kg', 250 ml -> '250 ml'."""
    q = Decimal(quantity)
    if base_unit in ('g', 'ml') and abs(q) >= 1000:
        big = 'kg' if base_unit == 'g' else 'l'
        return f'{_trim(q / 1000)} {big}'
    return f'{_trim(q)} {base_unit}'


def _trim(q):
    text = f'{q.quantize(Decimal("0.001")):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text
