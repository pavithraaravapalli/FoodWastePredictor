"""Ingredient stock: every change is an immutable StockTransaction posted inside a database transaction.

Rules
* Only ACTUAL consumption (what staff recorded) is deducted - never planned or predicted amounts.
* A batch is deducted once per submission. The reference "BATCH<id>-S<n>-I<ingredient>" is UNIQUE, so even a
  double click, a page refresh or a repeated request cannot deduct twice (the second attempt is refused).
* History is never edited or deleted. A correction posts REVERSAL rows (the opposite sign, pointing at the
  original) and then the batch is resubmitted, which posts fresh consumption rows.
* Stock may go negative if the kitchen used more than the books say. That is shown as a warning
  ("recount needed") instead of silently refusing the truth.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Ingredient, IngredientUsage, PreparationBatch, StockTransaction
from . import units

TX = StockTransaction


class StockError(Exception):
    """A readable reason an operation was refused."""


def post(ingredient_id, kind, delta, reference, *, user=None, reason='', batch=None, reverses=None, unit_cost=None):
    """Apply one signed change (base unit) to an ingredient and record it. Call inside transaction.atomic()."""
    ing = Ingredient.objects.select_for_update().get(pk=ingredient_id)
    delta = Decimal(delta)
    ing.current_stock = ing.current_stock + delta
    try:
        with transaction.atomic():
            tx = TX.objects.create(ingredient=ing, kind=kind, quantity_change=delta, balance_after=ing.current_stock,
                                   reference=reference, reason=reason[:300], batch=batch, reverses=reverses,
                                   created_by=user, unit_cost=unit_cost if unit_cost is not None else ing.cost_per_unit)
    except IntegrityError:
        raise StockError('That stock movement was already recorded.') from None
    ing.save(update_fields=['current_stock'])
    return tx


def _ref(prefix):
    return f'{prefix}-{timezone.now():%Y%m%d%H%M%S%f}'


@transaction.atomic
def record_purchase(ingredient, quantity, unit, user, cost_per_unit=None, reason=''):
    base = units.to_base(quantity, unit)
    if base <= 0:
        raise StockError('Enter a quantity greater than zero.')
    ing = Ingredient.objects.select_for_update().get(pk=ingredient.pk)
    if not units.compatible(unit, ing.base_unit):
        raise StockError(f'{ing.name} is measured in {ing.base_unit}; {unit} cannot be used.')
    if cost_per_unit is not None:
        ing.cost_per_unit = cost_per_unit
        ing.save(update_fields=['cost_per_unit'])
    return post(ing.pk, TX.PURCHASE, base, _ref('PUR'), user=user, reason=reason or 'Purchase')


@transaction.atomic
def adjust_stock(ingredient, new_quantity, unit, user, reason):
    """Set stock to a counted value; the difference is recorded with the reason."""
    if not reason.strip():
        raise StockError('A reason is required for a stock adjustment.')
    ing = Ingredient.objects.select_for_update().get(pk=ingredient.pk)
    if not units.compatible(unit, ing.base_unit):
        raise StockError(f'{ing.name} is measured in {ing.base_unit}; {unit} cannot be used.')
    target = units.to_base(new_quantity, unit)
    if target < 0:
        raise StockError('Stock cannot be counted as negative.')
    delta = target - ing.current_stock
    if delta == 0:
        raise StockError('That is already the recorded stock level.')
    return post(ing.pk, TX.ADJUSTMENT, delta, _ref('ADJ'), user=user, reason=reason)


def build_usages(batch):
    """Create the expected-usage lines for a batch from its food item's recipe. Returns the number of lines."""
    recipe = getattr(batch.food_item, 'recipe', None)
    if recipe is None or not recipe.is_active or not recipe.lines.exists():
        raise StockError(f'{batch.food_item.name} has no recipe yet. Ask the owner or a manager to create one.')
    n = 0
    for line in recipe.lines.select_related('ingredient'):
        expected = (line.base_quantity_per_portion * batch.planned_quantity).quantize(Decimal('0.001'))
        IngredientUsage.objects.update_or_create(
            batch=batch, ingredient=line.ingredient,
            defaults={'expected_quantity': expected, 'unit': line.ingredient.base_unit})
        n += 1
    return n


@transaction.atomic
def submit_batch(batch_id, user):
    """Deduct the batch's ACTUAL ingredient usage exactly once and mark it submitted."""
    batch = PreparationBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == PreparationBatch.SUBMITTED:
        raise StockError('This batch was already submitted. Stock has been deducted once.')
    usages = list(batch.usages.select_related('ingredient'))
    if not usages:
        raise StockError('This batch has no ingredient lines.')
    if batch.actual_quantity is None:
        raise StockError('Record how many portions were actually prepared.')
    missing = [u.ingredient.name for u in usages if u.actual_quantity is None]
    if missing:
        raise StockError('Record the actual quantity used for: ' + ', '.join(missing) + ' (enter 0 if none was used).')
    batch.submission_count += 1
    now = timezone.now()
    for u in usages:
        if u.actual_quantity > 0:
            post(u.ingredient_id, TX.CONSUMPTION, -u.actual_quantity,
                 f'BATCH{batch.pk}-S{batch.submission_count}-I{u.ingredient_id}',
                 user=user, batch=batch, reason=f'Used for {batch.food_item.name} batch {batch.date:%d-%m-%Y}')
    batch.status = PreparationBatch.SUBMITTED
    batch.submitted_by, batch.submitted_at, batch.correction_reason = user, now, ''
    batch.save(update_fields=['submission_count', 'status', 'submitted_by', 'submitted_at', 'correction_reason'])
    return batch


@transaction.atomic
def request_correction(batch_id, user, reason):
    """Reverse the batch's consumption (originals stay in the history) and reopen it for the staff member."""
    if not reason.strip():
        raise StockError('Give a reason for the correction.')
    batch = PreparationBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status != PreparationBatch.SUBMITTED:
        raise StockError('Only a submitted batch can be sent back for correction.')
    originals = batch.transactions.filter(kind=TX.CONSUMPTION, reversed_by__isnull=True)
    for tx in originals:
        post(tx.ingredient_id, TX.REVERSAL, -tx.quantity_change, f'REV-{tx.reference}', user=user,
             batch=batch, reverses=tx, reason=f'Correction: {reason}')
    batch.status, batch.correction_reason = PreparationBatch.CORRECTION, reason[:300]
    batch.save(update_fields=['status', 'correction_reason'])
    return batch
