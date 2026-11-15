"""Ingredients, recipes, stock, preparation batches and kitchen consumption."""
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import MSG_DENIED, log_activity, permission_required
from .kitchen_forms import (AdjustForm, BatchCreateForm, IngredientForm, PurchaseForm, RecipeForm, RecipeLineFormSet)
from .models import (FoodItem, Ingredient, IngredientUsage, Prediction, PreparationBatch, Recipe, StockTransaction)
from .services import requirements as req_svc
from .services import stock as stock_svc
from .services import units
from .services.records import parse_date

User = get_user_model()
ZERO = Decimal('0')


def _show_cost(user):
    return user.has_perm('predictor.view_reports')


def _problems(form):
    return '; '.join(f'{e}' for errs in form.errors.values() for e in errs)


# ------------------------------------------------------------------ ingredients
@permission_required('view_ingredient')
def ingredients(request):
    qs = Ingredient.objects.all()
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q) | Q(supplier__icontains=q))
    category = request.GET.get('category') or ''
    if category:
        qs = qs.filter(category__iexact=category)
    state = request.GET.get('state') or 'active'
    if state == 'active':
        qs = qs.filter(is_active=True)
    elif state == 'inactive':
        qs = qs.filter(is_active=False)
    rows = list(qs)
    if request.GET.get('stock') == 'low':
        rows = [i for i in rows if i.stock_status != 'ok']
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    return render(request, 'predictor/ingredients.html', {
        'page': page, 'filters': {'q': q, 'category': category, 'state': state, 'stock': request.GET.get('stock', '')},
        'filtered': bool(q or category or request.GET.get('stock') or state != 'active'),
        'categories': Ingredient.objects.exclude(category='').values_list('category', flat=True).distinct().order_by('category'),
        'can_add': request.user.has_perm('predictor.add_ingredient'),
        'can_change': request.user.has_perm('predictor.change_ingredient'),
        'show_cost': _show_cost(request.user),
    })


@permission_required('add_ingredient')
def ingredient_add(request):
    form = IngredientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            ing = form.save()
            opening = form.opening_base()
            if opening:
                stock_svc.post(ing.pk, StockTransaction.OPENING, opening, f'OPEN-{ing.pk}', user=request.user, reason='Opening stock')
        log_activity(request.user, 'ingredient', ing, new={'name': ing.name, 'unit': ing.base_unit}, summary='Ingredient added')
        messages.success(request, f'Added {ing.name}.')
        return redirect('ingredient_detail', pk=ing.pk)
    return render(request, 'predictor/ingredient_form.html', {'form': form, 'ingredient': None})


@permission_required('change_ingredient')
def ingredient_edit(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk)
    form = IngredientForm(request.POST or None, instance=ing)
    if request.method == 'POST' and form.is_valid():
        old = {'min_stock': ing.min_stock, 'cost': ing.cost_per_unit, 'name': ing.name, 'supplier': ing.supplier}
        ing = form.save()
        log_activity(request.user, 'ingredient', ing, old=old,
                     new={'min_stock': ing.min_stock, 'cost': ing.cost_per_unit, 'name': ing.name, 'supplier': ing.supplier},
                     summary='Ingredient changed')
        messages.success(request, f'Saved {ing.name}.')
        return redirect('ingredient_detail', pk=ing.pk)
    return render(request, 'predictor/ingredient_form.html', {'form': form, 'ingredient': ing})


@permission_required('change_ingredient')
@require_POST
def ingredient_toggle(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk)
    ing.is_active = not ing.is_active
    ing.save(update_fields=['is_active'])
    log_activity(request.user, 'ingredient', ing, new={'active': ing.is_active}, summary='Ingredient activated' if ing.is_active else 'Ingredient deactivated')
    messages.success(request, f'{ing.name} is now {"active" if ing.is_active else "inactive"}. Its stock history is kept.')
    return redirect(request.POST.get('next') or 'ingredients')


@permission_required('view_ingredient')
def ingredient_detail(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk)
    history = Paginator(ing.transactions.select_related('created_by', 'batch'), 15).get_page(request.GET.get('page'))
    used_in = ing.recipe_lines.select_related('recipe__food_item')
    since = timezone.localdate() - timedelta(days=30)
    usage = IngredientUsage.objects.filter(ingredient=ing, batch__status='submitted', batch__date__gte=since)
    used30 = sum((u.actual_quantity or ZERO for u in usage), ZERO)
    return render(request, 'predictor/ingredient_detail.html', {
        'ing': ing, 'history': history, 'used_in': used_in, 'used30': used30,
        'purchase_form': PurchaseForm(initial={'unit': {'g': 'kg', 'ml': 'l', 'pcs': 'pcs'}[ing.base_unit]}),
        'adjust_form': AdjustForm(initial={'unit': {'g': 'kg', 'ml': 'l', 'pcs': 'pcs'}[ing.base_unit]}),
        'can_stock': request.user.has_perm('predictor.manage_stock'),
        'can_change': request.user.has_perm('predictor.change_ingredient'),
        'show_cost': _show_cost(request.user),
    })


@permission_required('manage_stock')
@require_POST
def ingredient_purchase(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk)
    form = PurchaseForm(request.POST)
    if not form.is_valid():
        messages.error(request, f'Purchase not recorded: {_problems(form)}')
    else:
        d = form.cleaned_data
        try:
            cost = None
            if d['cost'] is not None:
                cost = d['cost'] / units.UNITS[d['unit']][1]
            tx = stock_svc.record_purchase(ing, d['quantity'], d['unit'], request.user, cost_per_unit=cost, reason=d['note'])
            log_activity(request.user, 'stock', ing, new={'purchased': tx.quantity_change, 'balance': tx.balance_after}, summary='Stock purchase recorded')
            messages.success(request, f'Added {units.pretty(tx.quantity_change, ing.base_unit)} of {ing.name}. Stock is now {units.pretty(tx.balance_after, ing.base_unit)}.')
        except (stock_svc.StockError, units.UnitError) as e:
            messages.error(request, str(e))
    return redirect('ingredient_detail', pk=ing.pk)


@permission_required('manage_stock')
@require_POST
def ingredient_adjust(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk)
    form = AdjustForm(request.POST)
    if not form.is_valid():
        messages.error(request, f'Stock not adjusted: {_problems(form)}')
    else:
        d = form.cleaned_data
        try:
            tx = stock_svc.adjust_stock(ing, d['new_quantity'], d['unit'], request.user, d['reason'])
            log_activity(request.user, 'stock', ing, new={'change': tx.quantity_change, 'balance': tx.balance_after, 'reason': d['reason']},
                         summary='Stock adjusted')
            messages.success(request, f'Stock for {ing.name} adjusted to {units.pretty(tx.balance_after, ing.base_unit)}.')
        except (stock_svc.StockError, units.UnitError) as e:
            messages.error(request, str(e))
    return redirect('ingredient_detail', pk=ing.pk)


# ------------------------------------------------------------------ stock dashboard
@permission_required('view_ingredient')
def stock(request):
    items = list(Ingredient.objects.filter(is_active=True))
    low = [i for i in items if i.stock_status != 'ok']
    today = timezone.localdate()
    upcoming = Prediction.objects.filter(date__gte=today, date__lte=today + timedelta(days=3)).select_related('food_item')
    wanted = {}
    for p in upcoming:
        wanted[p.food_item] = wanted.get(p.food_item, 0) + p.quantity_to_prepare
    needs = req_svc.calculate(wanted)
    since = today - timedelta(days=30)
    trend = {}
    for u in IngredientUsage.objects.filter(batch__status='submitted', batch__date__gte=since).select_related('ingredient'):
        t = trend.setdefault(u.ingredient_id, {'ingredient': u.ingredient, 'actual': ZERO})
        t['actual'] += u.actual_quantity or ZERO
    top = sorted(trend.values(), key=lambda t: t['actual'], reverse=True)[:6]
    recent = StockTransaction.objects.select_related('ingredient', 'created_by')[:12]
    return render(request, 'predictor/stock.html', {
        'items': items, 'low': low, 'needs': needs, 'top': top, 'recent': recent,
        'value': sum((i.stock_value for i in items), ZERO), 'show_cost': _show_cost(request.user),
        'plan_days': f'{today:%d-%m-%Y} to {today + timedelta(days=3):%d-%m-%Y}',
        'can_stock': request.user.has_perm('predictor.manage_stock'),
    })


# ------------------------------------------------------------------ recipes
@permission_required('view_recipe')
def recipes(request):
    q = (request.GET.get('q') or '').strip()
    qs = Recipe.objects.select_related('food_item').prefetch_related('lines__ingredient')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(food_item__name__icontains=q))
    items_without = FoodItem.objects.filter(is_active=True, recipe__isnull=True).order_by('name')
    show_cost = _show_cost(request.user)
    rows = [{'recipe': r, 'lines': len(r.lines.all()), 'cost': r.cost_per_portion if show_cost else None} for r in qs]
    return render(request, 'predictor/recipes.html', {
        'rows': rows, 'without': items_without, 'q': q, 'show_cost': show_cost,
        'can_add': request.user.has_perm('predictor.add_recipe')})


@permission_required('view_recipe')
def recipe_detail(request, pk):
    recipe = get_object_or_404(Recipe.objects.select_related('food_item'), pk=pk)
    show_cost = _show_cost(request.user)
    return render(request, 'predictor/recipe_detail.html', {
        'recipe': recipe, 'rows': recipe.lines_with_cost(), 'show_cost': show_cost,
        'cost': recipe.cost_per_portion if show_cost else None,
        'can_change': request.user.has_perm('predictor.change_recipe')})


def _save_recipe(request, recipe):
    form = RecipeForm(request.POST or None, instance=recipe)
    formset = RecipeLineFormSet(request.POST or None, instance=recipe or Recipe())
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        formset = RecipeLineFormSet(request.POST, instance=saved)
        if formset.is_valid():
            live = [f for f in formset.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]
            if not live:
                form.add_error(None, 'Add at least one ingredient to the recipe.')
            else:
                bad = False
                for f in live:
                    try:
                        units.dimension(f.cleaned_data['unit'])
                        if not units.compatible(f.cleaned_data['unit'], f.cleaned_data['ingredient'].base_unit):
                            f.add_error('unit', f'{f.cleaned_data["ingredient"].name} is measured in {f.cleaned_data["ingredient"].base_unit}; '
                                                f'{f.cleaned_data["unit"]} cannot be used for it.')
                            bad = True
                    except units.UnitError as e:
                        f.add_error('unit', str(e)); bad = True
                if not bad:
                    return saved, form, formset
    return None, form, formset


@permission_required('add_recipe')
def recipe_add(request):
    saved, form, formset = _save_recipe(request, None)
    if saved is not None:
        with transaction.atomic():
            saved.save()
            formset.instance = saved
            formset.save()
        log_activity(request.user, 'recipe', saved, new={'item': saved.food_item.name, 'yield': saved.yield_quantity}, summary='Recipe created')
        messages.success(request, f'Recipe for {saved.food_item.name} saved.')
        return redirect('recipe_detail', pk=saved.pk)
    initial_item = request.GET.get('item')
    if request.method != 'POST' and (initial_item or '').isdigit():
        form.fields['food_item'].initial = int(initial_item)
    return render(request, 'predictor/recipe_form.html', {'form': form, 'formset': formset, 'recipe': None})


@permission_required('change_recipe')
def recipe_edit(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    old = {'name': recipe.name, 'yield': recipe.yield_quantity,
           'lines': '; '.join(f'{l.ingredient.name} {l.quantity}{l.unit}' for l in recipe.lines.select_related('ingredient'))}
    saved, form, formset = _save_recipe(request, recipe)
    if saved is not None:
        with transaction.atomic():
            saved.save()
            formset.save()
        new = {'name': saved.name, 'yield': saved.yield_quantity,
               'lines': '; '.join(f'{l.ingredient.name} {l.quantity}{l.unit}' for l in saved.lines.select_related('ingredient'))}
        log_activity(request.user, 'recipe', saved, old=old, new=new, summary='Recipe changed')
        messages.success(request, 'Recipe updated. Future forecasts use the new quantities; submitted batches are unchanged.')
        return redirect('recipe_detail', pk=saved.pk)
    return render(request, 'predictor/recipe_form.html', {'form': form, 'formset': formset, 'recipe': recipe})


@permission_required('change_recipe')
@require_POST
def recipe_toggle(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    recipe.is_active = not recipe.is_active
    recipe.save(update_fields=['is_active'])
    log_activity(request.user, 'recipe', recipe, new={'active': recipe.is_active}, summary='Recipe activated' if recipe.is_active else 'Recipe deactivated')
    messages.success(request, f'Recipe is now {"active" if recipe.is_active else "inactive"}.')
    return redirect('recipe_detail', pk=recipe.pk)


# ------------------------------------------------------------------ kitchen batches
def _can_record(user, batch):
    """Only the assigned person edits, and only before submission (or after a correction was requested)."""
    return (user.has_perm('predictor.record_consumption') and batch.assigned_to_id == user.id
            and batch.status in (PreparationBatch.PLANNED, PreparationBatch.CORRECTION))


def _visible_batches(user):
    qs = PreparationBatch.objects.select_related('food_item', 'assigned_to')
    if user.has_perm('predictor.review_consumption'):
        return qs
    return qs.filter(assigned_to=user)


@permission_required('view_preparationbatch')
def kitchen(request):
    qs = _visible_batches(request.user)
    status = request.GET.get('status') or ''
    if status in dict(PreparationBatch.STATUSES):
        qs = qs.filter(status=status)
    day = parse_date(request.GET.get('date'))
    if day:
        qs = qs.filter(date=day)
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(food_item__name__icontains=q)
    page = Paginator(qs, 15).get_page(request.GET.get('page'))
    return render(request, 'predictor/kitchen.html', {
        'page': page, 'filters': {'status': status, 'date': day.isoformat() if day else '', 'q': q},
        'filtered': bool(status or day or q), 'statuses': PreparationBatch.STATUSES,
        'is_reviewer': request.user.has_perm('predictor.review_consumption'),
        'can_add': request.user.has_perm('predictor.add_preparationbatch'),
        'waiting': _visible_batches(request.user).filter(status=PreparationBatch.SUBMITTED).count()
                   if request.user.has_perm('predictor.review_consumption') else 0,
    })


@permission_required('add_preparationbatch')
def batch_add(request):
    initial = {'date': timezone.localdate()}
    plan = None
    if (request.GET.get('plan') or '').isdigit():
        plan = Prediction.objects.filter(pk=int(request.GET['plan'])).select_related('food_item').first()
        if plan:
            initial.update({'food_item': plan.food_item_id, 'date': plan.date, 'planned_quantity': plan.quantity_to_prepare})
    form = BatchCreateForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        batch = form.save(commit=False)
        batch.created_by = request.user
        batch.plan = Prediction.objects.filter(food_item=batch.food_item, date=batch.date).first()
        try:
            with transaction.atomic():
                batch.save()
                stock_svc.build_usages(batch)
        except stock_svc.StockError as e:
            form.add_error('food_item', str(e))
        else:
            log_activity(request.user, 'batch_create', batch, new={'item': batch.food_item.name, 'planned': batch.planned_quantity,
                                                                 'assigned': batch.assigned_to.get_username()}, summary='Preparation batch created')
            messages.success(request, f'Batch created and assigned to {batch.assigned_to.get_full_name() or batch.assigned_to.get_username()}.')
            return redirect('batch_detail', pk=batch.pk)
    return render(request, 'predictor/batch_form.html', {'form': form, 'plan': plan})


def _get_batch(request, pk):
    batch = get_object_or_404(PreparationBatch.objects.select_related('food_item', 'assigned_to', 'submitted_by'), pk=pk)
    if not request.user.has_perm('predictor.review_consumption') and batch.assigned_to_id != request.user.id:
        raise PermissionDenied(MSG_DENIED)
    return batch


@permission_required('view_preparationbatch')
def batch_detail(request, pk):
    batch = _get_batch(request, pk)
    usages = list(batch.usages.select_related('ingredient'))
    recipe = getattr(batch.food_item, 'recipe', None)
    return render(request, 'predictor/batch_detail.html', {
        'batch': batch, 'usages': usages, 'recipe': recipe, 'can_record': _can_record(request.user, batch),
        'can_review': request.user.has_perm('predictor.review_consumption') and batch.is_submitted,
        'show_cost': _show_cost(request.user),
        'transactions': batch.transactions.select_related('ingredient', 'created_by') if request.user.has_perm('predictor.view_stocktransaction') else [],
        'unit_choices': {u.ingredient_id: [(k, l) for k, l in units.UNIT_CHOICES if units.compatible(k, u.unit)] for u in usages},
    })


def _read_decimal(raw):
    raw = (raw or '').strip()
    if raw == '':
        return None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        raise ValueError('Quantities must be numbers.')
    if v < 0 or v > Decimal('1000000'):
        raise ValueError('Quantities must be between 0 and 1,000,000.')
    return v


def _save_usage(request, batch):
    """Store what the staff member typed. Returns an error message or None."""
    notes = (request.POST.get('notes') or '').strip()[:300]
    try:
        made = _read_decimal(request.POST.get('actual_quantity'))
        if made is not None and made != made.to_integral_value():
            raise ValueError('Portions prepared must be a whole number.')
        updates = []
        for u in batch.usages.select_related('ingredient'):
            unit = request.POST.get(f'unit_{u.pk}') or u.unit
            if not units.compatible(unit, u.unit):
                raise ValueError(f'{u.ingredient.name}: {unit} cannot be used (measured in {u.unit}).')
            actual, waste = _read_decimal(request.POST.get(f'actual_{u.pk}')), _read_decimal(request.POST.get(f'waste_{u.pk}'))
            u.actual_quantity = None if actual is None else units.convert(actual, unit, u.unit)
            u.waste_quantity = None if waste is None else units.convert(waste, unit, u.unit)
            updates.append(u)
    except (ValueError, units.UnitError) as e:
        return str(e)
    now = timezone.now()
    with transaction.atomic():
        for u in updates:
            u.recorded_by, u.recorded_at = request.user, now
            u.save(update_fields=['actual_quantity', 'waste_quantity', 'recorded_by', 'recorded_at'])
        batch.actual_quantity = None if made is None else int(made)
        batch.notes = notes or batch.notes
        batch.save(update_fields=['actual_quantity', 'notes'])
    return None


@permission_required('record_consumption')
@require_POST
def batch_save(request, pk):
    batch = _get_batch(request, pk)
    if not _can_record(request.user, batch):
        messages.error(request, 'This batch is submitted or not assigned to you, so it cannot be edited.')
        return redirect('batch_detail', pk=pk)
    err = _save_usage(request, batch)
    messages.error(request, err) if err else messages.success(request, 'Progress saved. Submit the batch when every ingredient is recorded.')
    return redirect('batch_detail', pk=pk)


@permission_required('record_consumption')
@require_POST
def batch_submit(request, pk):
    batch = _get_batch(request, pk)
    if not _can_record(request.user, batch):
        messages.error(request, 'This batch has already been submitted or is not assigned to you.')
        return redirect('batch_detail', pk=pk)
    err = _save_usage(request, batch)
    if err:
        messages.error(request, err)
        return redirect('batch_detail', pk=pk)
    try:
        batch = stock_svc.submit_batch(batch.pk, request.user)
    except stock_svc.StockError as e:
        messages.error(request, str(e))
        return redirect('batch_detail', pk=pk)
    used = {u.ingredient.name: float(u.actual_quantity) for u in batch.usages.select_related('ingredient')}
    log_activity(request.user, 'batch_submit', batch, new={'prepared': batch.actual_quantity, 'used': used}, summary='Batch submitted; stock deducted')
    messages.success(request, 'Batch submitted. The ingredients you used have been deducted from stock.')
    return redirect('batch_detail', pk=pk)


@permission_required('review_consumption')
@require_POST
def batch_correct(request, pk):
    batch = _get_batch(request, pk)
    reason = (request.POST.get('reason') or '').strip()
    try:
        stock_svc.request_correction(batch.pk, request.user, reason)
    except stock_svc.StockError as e:
        messages.error(request, str(e))
    else:
        log_activity(request.user, 'batch_review', batch, new={'action': 'correction requested', 'reason': reason},
                     summary='Batch sent back for correction; stock reversed')
        messages.success(request, 'Stock was restored with reversal entries and the batch is back with the staff member for correction.')
    return redirect('batch_detail', pk=pk)


# ------------------------------------------------------------------ consumption analytics
@permission_required('review_consumption')
def consumption(request):
    today = timezone.localdate()
    end = parse_date(request.GET.get('end')) or today
    start = parse_date(request.GET.get('start')) or end - timedelta(days=29)
    if start > end:
        start, end = end, start
    usages = (IngredientUsage.objects.filter(batch__status='submitted', batch__date__range=(start, end))
              .select_related('ingredient', 'batch__food_item', 'batch__submitted_by'))
    by_ing, by_staff = {}, {}
    for u in usages:
        a = u.actual_quantity or ZERO
        row = by_ing.setdefault(u.ingredient_id, {'ingredient': u.ingredient, 'expected': ZERO, 'actual': ZERO, 'waste': ZERO})
        row['expected'] += u.expected_quantity
        row['actual'] += a
        row['waste'] += u.waste_quantity or ZERO
        who = u.batch.submitted_by.get_full_name() or u.batch.submitted_by.get_username() if u.batch.submitted_by else 'Unknown'
        s = by_staff.setdefault(who, {'name': who, 'batches': set(), 'over': ZERO})
        s['batches'].add(u.batch_id)
    rows = []
    for r in by_ing.values():
        ing = r['ingredient']
        r['difference'] = r['actual'] - r['expected']
        r['cost'] = r['actual'] * ing.cost_per_unit
        r['remaining'] = ing.current_stock
        r['sentence'] = _explain(r)
        rows.append(r)
    rows.sort(key=lambda r: abs(r['difference']), reverse=True)
    chart = {'labels': [r['ingredient'].name for r in rows[:10]],
             'unit': 'base units (g / ml / pcs)',
             'expected': [float(r['expected']) for r in rows[:10]], 'actual': [float(r['actual']) for r in rows[:10]]}
    staff = [{'name': s['name'], 'batches': len(s['batches'])} for s in by_staff.values()]
    batches = (PreparationBatch.objects.filter(status='submitted', date__range=(start, end))
               .select_related('food_item', 'submitted_by').order_by('-date')[:25])
    return render(request, 'predictor/consumption.html', {
        'rows': rows, 'chart': json.dumps(chart), 'staff': staff, 'batches': batches, 'start': start, 'end': end,
        'show_cost': _show_cost(request.user), 'total_cost': sum((r['cost'] for r in rows), ZERO)})


def _explain(r):
    ing, diff = r['ingredient'], r['difference']
    if r['expected'] == 0 and r['actual'] == 0:
        return 'Nothing used.'
    if diff == 0:
        return 'Used exactly what the recipe expected.'
    word = 'exceeded' if diff > 0 else 'was below'
    return f'Actual usage {word} the recipe estimate by {units.pretty(abs(diff), ing.base_unit)}.'
