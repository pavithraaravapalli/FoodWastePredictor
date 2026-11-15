"""Ingredients, recipes, ingredient forecasting, kitchen consumption and stock deduction."""
from datetime import timedelta
from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import (ActivityLog, FoodItem, Ingredient, IngredientUsage, Prediction, PreparationBatch, Recipe,
                     RecipeIngredient, StockTransaction)
from .services import requirements as req_svc
from .services import stock as stock_svc
from .services import units
from .tests import RoleTestCase


class UnitTests(RoleTestCase):
    def test_conversions(self):
        self.assertEqual(units.convert(D('4'), 'kg', 'g'), D('4000'))
        self.assertEqual(units.convert(D('250'), 'ml', 'l'), D('0.25'))
        self.assertEqual(units.to_base(D('1.5'), 'l'), D('1500'))

    def test_incompatible_units_are_refused(self):
        for a, b in (('g', 'ml'), ('kg', 'l'), ('pcs', 'g'), ('l', 'pcs')):
            with self.assertRaises(units.UnitError):
                units.convert(D('1'), a, b)
        with self.assertRaises(units.UnitError):
            units.dimension('stone')

    def test_pretty(self):
        self.assertEqual(units.pretty(D('4300'), 'g'), '4.3 kg')
        self.assertEqual(units.pretty(D('250'), 'ml'), '250 ml')
        self.assertEqual(units.pretty(D('12'), 'pcs'), '12 pcs')


class KitchenBase(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.samosa = FoodItem.objects.create(name='Samosa', category='Snack', cost_per_portion=10)
        self.potato = Ingredient.objects.create(name='Potato', base_unit='g', cost_per_unit=D('0.04'), min_stock=5000)
        self.peas = Ingredient.objects.create(name='Peas', base_unit='g', cost_per_unit=D('0.1'))
        self.oil = Ingredient.objects.create(name='Oil', base_unit='ml', cost_per_unit=D('0.15'))
        self.recipe = Recipe.objects.create(food_item=self.samosa, name='Samosa', yield_quantity=1)
        RecipeIngredient.objects.create(recipe=self.recipe, ingredient=self.potato, quantity=40, unit='g')
        RecipeIngredient.objects.create(recipe=self.recipe, ingredient=self.peas, quantity=10, unit='g')
        RecipeIngredient.objects.create(recipe=self.recipe, ingredient=self.oil, quantity=8, unit='ml')
        stock_svc.post(self.potato.pk, 'opening', D('20000'), 'OPEN-P', user=self.owner)

    def batch(self, qty=100, user=None):
        b = PreparationBatch.objects.create(food_item=self.samosa, date=timezone.localdate(), planned_quantity=qty,
                                            assigned_to=user or self.staff, created_by=self.manager)
        stock_svc.build_usages(b)
        return b

    def fill(self, b, potato=4300, peas=1100, oil=800, made=100):
        for u in b.usages.all():
            u.actual_quantity = {'Potato': potato, 'Peas': peas, 'Oil': oil}[u.ingredient.name]
            u.save()
        b.actual_quantity = made
        b.save()


class RecipeCalculationTests(KitchenBase):
    def test_per_portion_uses_yield_and_loss(self):
        r = Recipe.objects.create(food_item=FoodItem.objects.create(name='Pulao', category='M', cost_per_portion=1), name='Pulao', yield_quantity=4)
        line = RecipeIngredient.objects.create(recipe=r, ingredient=self.potato, quantity=2, unit='kg', loss_percent=20)
        self.assertEqual(line.base_quantity_per_portion, D('625'))        # 2000 g / 0.8 / 4 portions

    def test_incompatible_unit_in_recipe_is_rejected_by_the_form(self):
        c = self.as_user(self.manager)
        item = FoodItem.objects.create(name='Chai', category='D', cost_per_portion=1)
        data = {'food_item': item.pk, 'name': 'Chai', 'yield_quantity': 1, 'instructions': '',
                'lines-TOTAL_FORMS': 1, 'lines-INITIAL_FORMS': 0, 'lines-MIN_NUM_FORMS': 0, 'lines-MAX_NUM_FORMS': 1000,
                'lines-0-ingredient': self.oil.pk, 'lines-0-quantity': 5, 'lines-0-unit': 'g', 'lines-0-loss_percent': 0}
        r = c.post(reverse('recipe_add'), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'cannot be used')
        self.assertFalse(Recipe.objects.filter(food_item=item).exists())

    def test_ingredient_demand_for_100_samosas(self):
        out = req_svc.calculate({self.samosa: 100})
        need = {s['ingredient'].name: s['required'] for s in out['shopping']}
        self.assertEqual(need, {'Potato': D('4000.000'), 'Peas': D('1000.000'), 'Oil': D('800.000')})
        short = {s['ingredient'].name: s['shortage'] for s in out['shopping']}
        self.assertEqual(short['Potato'], 0)                      # 20 kg in stock
        self.assertEqual(short['Peas'], D('1000.000'))            # none in stock

    def test_safety_reserve_and_missing_recipe(self):
        other = FoodItem.objects.create(name='Jalebi', category='Sweet', cost_per_portion=5)
        out = req_svc.calculate({self.samosa: 100, other: 10}, reserve_percent=10)
        self.assertEqual(out['missing_recipes'], [other])         # nothing invented for it
        self.assertEqual({s['ingredient'].name: s['required'] for s in out['shopping']}['Potato'], D('4400.000'))


class StockDeductionTests(KitchenBase):
    def test_submission_deducts_actual_not_planned_and_is_recorded(self):
        b = self.batch()
        self.fill(b)
        stock_svc.submit_batch(b.pk, self.staff)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('15700'))     # 20 kg - 4.3 kg
        tx = StockTransaction.objects.get(batch=b, ingredient=self.potato)
        self.assertEqual((tx.kind, tx.quantity_change, tx.created_by), ('consumption', D('-4300'), self.staff))

    def test_duplicate_submission_cannot_deduct_twice(self):
        b = self.batch(); self.fill(b)
        stock_svc.submit_batch(b.pk, self.staff)
        with self.assertRaises(stock_svc.StockError):
            stock_svc.submit_batch(b.pk, self.staff)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('15700'))
        self.assertEqual(StockTransaction.objects.filter(batch=b, kind='consumption').count(), 3)

    def test_reference_is_unique_in_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockTransaction.objects.create(ingredient=self.potato, kind='purchase', quantity_change=1, balance_after=1, reference='OPEN-P')

    def test_double_post_over_http_deducts_once(self):
        b = self.batch(); self.fill(b)
        c = self.as_user(self.staff)
        c.post(reverse('batch_submit', args=[b.pk]), {'actual_quantity': 100, **{f'actual_{u.pk}': 1 for u in b.usages.all()}})
        c.post(reverse('batch_submit', args=[b.pk]), {'actual_quantity': 100, **{f'actual_{u.pk}': 99999 for u in b.usages.all()}})
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('19999'))     # only the first submission counted

    def test_incomplete_batch_is_refused(self):
        b = self.batch()
        with self.assertRaises(stock_svc.StockError):
            stock_svc.submit_batch(b.pk, self.staff)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('20000'))

    def test_reversal_and_corrected_resubmission_preserve_history(self):
        b = self.batch(); self.fill(b)
        stock_svc.submit_batch(b.pk, self.staff)
        stock_svc.request_correction(b.pk, self.manager, 'Weighed wrongly')
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('20000'))     # restored
        self.assertEqual(StockTransaction.objects.filter(batch=b, kind='consumption').count(), 3)   # originals kept
        self.assertEqual(StockTransaction.objects.filter(batch=b, kind='reversal').count(), 3)
        b.refresh_from_db()
        self.assertEqual(b.status, 'correction')
        self.fill(b, potato=4000)
        stock_svc.submit_batch(b.pk, self.staff)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('16000'))
        self.assertEqual(StockTransaction.objects.filter(ingredient=self.potato).count(), 4)       # open, use, reverse, use

    def test_purchase_adjust_and_low_stock(self):
        stock_svc.record_purchase(self.peas, D('2'), 'kg', self.manager, reason='Bill 12')
        self.peas.refresh_from_db()
        self.assertEqual(self.peas.current_stock, D('2000'))
        with self.assertRaises(stock_svc.StockError):
            stock_svc.record_purchase(self.peas, D('1'), 'l', self.manager)   # litres cannot be added to a gram ingredient
        with self.assertRaises(stock_svc.StockError):
            stock_svc.adjust_stock(self.peas, D('1'), 'kg', self.manager, '')
        stock_svc.adjust_stock(self.peas, D('1'), 'kg', self.manager, 'Spoilage')
        self.peas.refresh_from_db()
        self.assertEqual(self.peas.current_stock, D('1000'))
        stock_svc.post(self.potato.pk, 'adjustment', D('-16000'), 'ADJ-LOW', user=self.manager)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.stock_status, 'low')
        page = self.as_user(self.manager).get(reverse('stock'))
        self.assertContains(page, 'Low-stock alerts')
        self.assertContains(page, 'Potato')

    def test_batch_without_recipe_is_refused(self):
        item = FoodItem.objects.create(name='Jalebi', category='Sweet', cost_per_portion=5)
        b = PreparationBatch.objects.create(food_item=item, date=timezone.localdate(), planned_quantity=5, assigned_to=self.staff)
        with self.assertRaises(stock_svc.StockError):
            stock_svc.build_usages(b)


class KitchenWorkflowAndPermissionTests(KitchenBase):
    def test_full_workflow_through_the_pages(self):
        Prediction.objects.create(food_item=self.samosa, date=timezone.localdate(), predicted_demand=90,
                                  recommended_preparation=100, approved_quantity=100, status='approved')
        m = self.as_user(self.manager)
        self.assertContains(m.get(reverse('planner')), 'Ingredients needed')
        r = m.post(reverse('batch_add'), {'food_item': self.samosa.pk, 'date': timezone.localdate().isoformat(),
                                          'planned_quantity': 100, 'assigned_to': self.staff.pk, 'time_slot': 'Morning'})
        batch = PreparationBatch.objects.get()
        self.assertRedirects(r, reverse('batch_detail', args=[batch.pk]))
        self.assertEqual(batch.usages.count(), 3)
        s = self.as_user(self.staff)
        data = {'actual_quantity': 98, 'notes': 'ok'}
        for u in batch.usages.all():
            data[f'actual_{u.pk}'] = {'Potato': '4.3', 'Peas': '1.1', 'Oil': '800'}[u.ingredient.name]
            data[f'unit_{u.pk}'] = {'Potato': 'kg', 'Peas': 'kg', 'Oil': 'ml'}[u.ingredient.name]
        s.post(reverse('batch_submit', args=[batch.pk]), data)
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.actual_quantity), ('submitted', 98))
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('15700'))
        self.assertTrue(ActivityLog.objects.filter(action='batch_submit').exists())
        page = self.as_user(self.manager).get(reverse('consumption'))
        self.assertContains(page, 'exceeded the recipe estimate by 300 g')

    def test_staff_cannot_edit_after_submission_or_others_batches(self):
        b = self.batch(); self.fill(b)
        stock_svc.submit_batch(b.pk, self.staff)
        c = self.as_user(self.staff)
        c.post(reverse('batch_save', args=[b.pk]), {'actual_quantity': 1})
        b.refresh_from_db()
        self.assertEqual(b.actual_quantity, 100)
        mine = self.batch(user=self.other_staff)
        self.assertEqual(c.get(reverse('batch_detail', args=[mine.pk])).status_code, 403)
        self.assertEqual(c.post(reverse('batch_submit', args=[mine.pk])).status_code, 403)

    def test_staff_cannot_use_management_pages(self):
        c = self.as_user(self.staff)
        for name in ('ingredients', 'stock', 'consumption', 'batch_add', 'ingredient_add', 'recipe_add'):
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)
        self.assertEqual(c.post(reverse('ingredient_purchase', args=[self.potato.pk]), {'quantity': 1, 'unit': 'kg'}).status_code, 403)
        self.assertEqual(c.post(reverse('batch_correct', args=[self.batch().pk]), {'reason': 'x'}).status_code, 403)
        self.potato.refresh_from_db()
        self.assertEqual(self.potato.current_stock, D('20000'))

    def test_staff_can_read_recipes_without_costs(self):
        page = self.as_user(self.staff).get(reverse('recipe_detail', args=[self.recipe.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Potato')
        self.assertNotContains(page, 'Cost / portion')
        self.assertEqual(self.as_user(self.staff).get(reverse('recipe_edit', args=[self.recipe.pk])).status_code, 403)

    def test_manager_and_owner_can_manage_ingredients_and_stock(self):
        for user in (self.manager, self.owner):
            c = self.as_user(user)
            for name in ('ingredients', 'stock', 'recipes', 'kitchen', 'consumption'):
                self.assertEqual(c.get(reverse(name)).status_code, 200, (user, name))
        c = self.as_user(self.manager)
        c.post(reverse('ingredient_add'), {'name': 'Flour', 'category': 'Dry', 'base_unit': 'g', 'min_qty': 2, 'min_unit': 'kg',
                                           'price': 50, 'price_unit': 'kg', 'opening_qty': 10, 'opening_unit': 'kg'})
        flour = Ingredient.objects.get(name='Flour')
        self.assertEqual((flour.current_stock, flour.min_stock, flour.cost_per_unit), (D('10000'), D('2000'), D('0.05')))
        self.assertTrue(StockTransaction.objects.filter(ingredient=flour, kind='opening').exists())

    def test_stock_changes_persist_after_logout_and_login(self):
        b = self.batch(); self.fill(b)
        stock_svc.submit_batch(b.pk, self.staff)
        self.client.force_login(self.manager); self.client.logout()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('ingredient_detail', args=[self.potato.pk])), '15.7 kg')

    def test_anonymous_users_are_sent_to_login(self):
        self.client.logout()
        for name in ('ingredients', 'stock', 'recipes', 'kitchen'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
