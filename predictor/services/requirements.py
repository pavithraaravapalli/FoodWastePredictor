"""Stage 2 of the forecast: turn predicted/planned food quantities into raw-ingredient requirements.

required = quantity to prepare x (recipe quantity / recipe yield), grossed up for preparation loss,
converted to the ingredient's base unit. Food items without a recipe are listed separately - nothing
is estimated for them.
"""
from collections import OrderedDict
from decimal import Decimal

from ..models import FoodItem, Ingredient, Recipe
from . import units

ZERO = Decimal('0')


def calculate(quantities, reserve_percent=0):
    """quantities: {FoodItem: portions to prepare}. Returns dict with per-item lines, totals and missing recipes."""
    reserve = Decimal(str(reserve_percent or 0))
    factor = Decimal('1') + reserve / Decimal('100')
    per_item, missing = [], []
    totals = OrderedDict()
    for item, qty in quantities.items():
        qty = int(qty or 0)
        if qty <= 0:
            continue
        recipe = Recipe.objects.filter(food_item=item, is_active=True).first()
        lines = list(recipe.lines.select_related('ingredient')) if recipe else []
        if not lines:
            missing.append(item)
            continue
        rows = []
        for line in lines:
            need = (line.base_quantity_per_portion * qty * factor).quantize(Decimal('0.001'))
            ing = line.ingredient
            rows.append({'ingredient': ing, 'required': need, 'cost': need * ing.cost_per_unit,
                         'per_portion': line.base_quantity_per_portion})
            t = totals.setdefault(ing.pk, {'ingredient': ing, 'required': ZERO})
            t['required'] += need
        per_item.append({'item': item, 'quantity': qty, 'recipe': recipe, 'rows': rows,
                         'cost': sum((r['cost'] for r in rows), ZERO)})
    shopping = []
    for t in totals.values():
        ing = t['ingredient']
        short = max(t['required'] - max(ing.current_stock, ZERO), ZERO)
        shopping.append({'ingredient': ing, 'required': t['required'], 'available': ing.current_stock,
                         'shortage': short, 'to_buy_cost': short * ing.cost_per_unit,
                         'total_cost': t['required'] * ing.cost_per_unit})
    shopping.sort(key=lambda r: (r['shortage'] == 0, r['ingredient'].name))
    return {'items': per_item, 'missing_recipes': missing, 'shopping': shopping,
            'total_cost': sum((r['total_cost'] for r in shopping), ZERO),
            'purchase_cost': sum((r['to_buy_cost'] for r in shopping), ZERO), 'reserve_percent': reserve}


def pretty(q, ing):
    return units.pretty(q, ing.base_unit)
