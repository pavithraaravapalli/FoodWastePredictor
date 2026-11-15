from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import FoodItem, FoodRecord, UserProfile


class LoggedInTestCase(TestCase):
    """TestCase whose client is already signed in (all app pages need login)."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('owner', 'owner@example.com', 'pw-12345-xyz', first_name='Owner')
        UserProfile.objects.update_or_create(user=self.user, defaults={'role': 'owner'})
        self.client.login(username='owner', password='pw-12345-xyz')


def make_record(item, days_ago, prepared, sold):
    return FoodRecord.objects.create(
        food_item=item, date=timezone.localdate() - timedelta(days=days_ago),
        prepared_quantity=prepared, sold_quantity=sold, weather='Sunny')


class DashboardTests(LoggedInTestCase):
    def test_empty_database_shows_onboarding(self):
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Start by adding your menu')

    def test_items_but_no_records_prompts_first_record(self):
        FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, 'Log your first daily record')

    def test_totals_and_cost_use_real_data(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=Decimal('60'))
        make_record(item, 1, 50, 40)
        make_record(item, 2, 50, 45)
        r = self.client.get(reverse('dashboard'), {'range': '7'})
        t = r.context['totals']
        self.assertEqual((t['prepared'], t['sold'], t['waste']), (100, 85, 15))
        self.assertEqual(t['waste_pct'], 15.0)
        self.assertEqual(t['cost'], Decimal('900'))

    def test_old_data_falls_back_to_all_time(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        make_record(item, 60, 10, 8)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['range_key'], 'all')
        self.assertTrue(r.context['has_records'])

    def test_explicit_range_with_no_data_shows_empty_state(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        make_record(item, 60, 10, 8)
        r = self.client.get(reverse('dashboard'), {'range': '7'})
        self.assertContains(r, 'No records in this date range')

    def test_bad_custom_dates_do_not_crash(self):
        r = self.client.get(reverse('dashboard'), {'start': 'x', 'end': 'y'})
        self.assertEqual(r.status_code, 200)

    def test_high_waste_item_raises_alert(self):
        item = FoodItem.objects.create(name='Biryani', category='Main', cost_per_portion=80)
        make_record(item, 1, 20, 10)
        r = self.client.get(reverse('dashboard'), {'range': '7'})
        self.assertTrue(any('Biryani' in a['text'] for a in r.context['alerts']))


class AddRecordTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)

    def payload(self, **kw):
        data = {'food_item': self.item.pk, 'date': timezone.localdate().isoformat(),
                'prepared_quantity': 20, 'sold_quantity': 15, 'weather': 'Sunny'}
        data.update(kw)
        return data

    def test_get_form(self):
        self.assertEqual(self.client.get(reverse('add_record')).status_code, 200)

    def test_valid_post_saves_and_redirects(self):
        r = self.client.post(reverse('add_record'), self.payload())
        self.assertRedirects(r, reverse('dashboard'))
        self.assertEqual(FoodRecord.objects.count(), 1)

    def test_sold_cannot_exceed_prepared(self):
        r = self.client.post(reverse('add_record'), self.payload(sold_quantity=25))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(FoodRecord.objects.count(), 0)
        self.assertContains(r, 'Sold quantity cannot be greater')


class ShellTests(LoggedInTestCase):
    def test_logout_form_and_name_shown(self):
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, reverse('logout'))
        self.assertContains(r, 'Owner')

    def test_active_nav_state(self):
        r = self.client.get(reverse('add_record'))
        active = [i['label'] for i in r.context['nav_items'] if i['active']]
        self.assertEqual(active, ['Daily Records'])

    def test_sidebar_groups_structure_and_counts(self):
        r = self.client.get(reverse('dashboard'))
        self.assertIn('nav_groups', r.context)
        groups = r.context['nav_groups']
        group_ids = [g['id'] for g in groups]
        self.assertEqual(group_ids, ['main', 'operations', 'kitchen_stock', 'intelligence', 'management'])
        total_items = sum(len(g['items']) for g in groups)
        self.assertEqual(total_items, 19)

    def test_sidebar_active_group_and_item_highlighted(self):
        r = self.client.get(reverse('inventory'))
        groups = {g['id']: g for g in r.context['nav_groups']}
        self.assertTrue(groups['operations']['has_active'])
        self.assertFalse(groups['main']['has_active'])
        self.assertContains(r, 'has-active')
        self.assertContains(r, 'is-active')

    def test_all_19_sidebar_links_render_and_work(self):
        r = self.client.get(reverse('dashboard'))
        items = r.context['nav_items']
        self.assertEqual(len(items), 19)
        for item in items:
            url = reverse(item['url_name'])
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, f"Failed for {item['label']} ({item['url_name']})")


SIGNUP_EXTRA = {'accept_terms': 'on'}


class AuthTests(TestCase):
    def test_app_pages_redirect_anonymous_to_login(self):
        for name in ('dashboard', 'add_record'):
            r = self.client.get(reverse(name))
            self.assertRedirects(r, f"{reverse('login')}?next={reverse(name)}")

    def test_login_with_username_and_email(self):
        User.objects.create_user('chef', 'chef@example.com', 'pw-12345-xyz')
        for ident in ('chef', 'CHEF@example.com'):
            self.client.logout()
            r = self.client.post(reverse('login'), {'identifier': ident, 'password': 'pw-12345-xyz'})
            self.assertRedirects(r, reverse('dashboard'), fetch_redirect_response=False)

    def test_login_wrong_password_shows_error(self):
        User.objects.create_user('chef', 'chef@example.com', 'pw-12345-xyz')
        r = self.client.post(reverse('login'), {'identifier': 'chef', 'password': 'nope'})
        self.assertContains(r, 'do not match')

    def test_login_next_is_honoured_but_not_open_redirect(self):
        User.objects.create_user('chef', 'c@example.com', 'pw-12345-xyz')
        data = {'identifier': 'chef', 'password': 'pw-12345-xyz'}
        r = self.client.post(reverse('login'), {**data, 'next': reverse('add_record')})
        self.assertRedirects(r, reverse('add_record'), fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse('login'), {**data, 'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('dashboard'), fetch_redirect_response=False)

    def test_signup_creates_pending_staff_account(self):
        r = self.client.post(reverse('signup'), {**SIGNUP_EXTRA,
            'full_name': 'Asha Verma', 'email': 'Asha@Example.com',
            'password1': 'Sturdy-pass-928', 'password2': 'Sturdy-pass-928'})
        self.assertRedirects(r, reverse('login'), fetch_redirect_response=False)
        u = User.objects.get(email='asha@example.com')
        self.assertEqual((u.first_name, u.last_name), ('Asha', 'Verma'))
        self.assertFalse(u.is_active)                       # cannot sign in until the owner approves
        self.assertEqual((u.profile.role, u.profile.is_pending), ('staff', True))
        self.assertRedirects(self.client.get(reverse('dashboard')), '/login/?next=/', fetch_redirect_response=False)

    def test_signup_rejects_duplicate_email_weak_and_mismatched_passwords(self):
        User.objects.create_user('x', 'dup@example.com', 'pw-12345-xyz')
        base = {**SIGNUP_EXTRA, 'full_name': 'A', 'email': 'dup@example.com', 'password1': 'Sturdy-pass-928', 'password2': 'Sturdy-pass-928'}
        self.assertContains(self.client.post(reverse('signup'), base), 'already exists')
        self.assertContains(self.client.post(reverse('signup'), {**base, 'email': 'n@example.com', 'password1': '12345678', 'password2': '12345678'}), 'field-error')
        self.assertContains(self.client.post(reverse('signup'), {**base, 'email': 'n@example.com', 'password2': 'different'}), 'do not match')
        self.assertEqual(User.objects.count(), 1)

    def test_logout_requires_post(self):
        User.objects.create_user('chef', 'c@example.com', 'pw-12345-xyz')
        self.client.login(username='chef', password='pw-12345-xyz')
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.client.post(reverse('logout'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_forgot_password_flow(self):
        from django.core import mail
        User.objects.create_user('chef', 'chef@example.com', 'old-pass-12345')
        r = self.client.post(reverse('password_reset'), {'email': 'chef@example.com'})
        self.assertRedirects(r, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('example.com/password', mail.outbox[0].body.replace('chef@example.com', ''))
        link = [w for w in mail.outbox[0].body.split() if '/password-reset/' in w][0]
        path = link.split('testserver')[1]
        r = self.client.get(path, follow=True)       # token is swapped for a session URL
        r = self.client.post(r.request['PATH_INFO'], {'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77'})
        self.assertRedirects(r, reverse('password_reset_complete'))
        self.assertTrue(self.client.login(username='chef', password='Brand-new-pass-77'))

    def test_forgot_password_unknown_email_does_not_reveal_anything(self):
        from django.core import mail
        r = self.client.post(reverse('password_reset'), {'email': 'ghost@example.com'})
        self.assertRedirects(r, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_auth_pages_render_without_any_google_option(self):
        for name in ('login', 'signup', 'password_reset', 'password_reset_done', 'password_reset_complete'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ('login', 'signup'):
            self.assertNotContains(self.client.get(reverse(name)), 'Google</span>')
            self.assertNotContains(self.client.get(reverse(name)), 'btn-google')
        self.assertEqual(self.client.get('/accounts/google/login/').status_code, 404)


class ComparisonTests(LoggedInTestCase):
    def test_no_comparison_when_previous_period_has_sparse_data(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        for d in range(0, 7):
            make_record(item, d, 50, 45)          # 7 days in current window
        make_record(item, 8, 50, 40)               # 1 day in previous window
        r = self.client.get(reverse('dashboard'), {'range': '7'})
        self.assertFalse(r.context['has_previous'])

    def test_comparison_shown_with_comparable_coverage(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        for d in range(0, 14):
            make_record(item, d, 50, 45)
        r = self.client.get(reverse('dashboard'), {'range': '7'})
        self.assertTrue(r.context['has_previous'])


class InventoryTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.thali = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        self.dosa = FoodItem.objects.create(name='Dosa', category='Breakfast', cost_per_portion=35)

    def test_list_search_and_category_filter(self):
        r = self.client.get(reverse('inventory'), {'q': 'dos'})
        self.assertEqual([i.name for i in r.context['items']], ['Dosa'])
        r = self.client.get(reverse('inventory'), {'category': 'main'})
        self.assertEqual([i.name for i in r.context['items']], ['Thali'])

    def test_add_item_and_duplicate_name_rejected(self):
        r = self.client.post(reverse('food_item_add'), {'name': ' Paneer  Wrap ', 'category': 'Snacks', 'cost_per_portion': '45.50'})
        self.assertRedirects(r, reverse('inventory'))
        self.assertTrue(FoodItem.objects.filter(name='Paneer Wrap', is_active=True).exists())
        r = self.client.post(reverse('food_item_add'), {'name': 'thali', 'category': 'Main', 'cost_per_portion': '10'})
        self.assertContains(r, 'already exists')

    def test_negative_cost_rejected(self):
        r = self.client.post(reverse('food_item_add'), {'name': 'X', 'category': 'Y', 'cost_per_portion': '-1'})
        self.assertContains(r, 'field-error')
        self.assertFalse(FoodItem.objects.filter(name='X').exists())

    def test_edit_item(self):
        self.client.post(reverse('food_item_edit', args=[self.dosa.pk]), {'name': 'Dosa', 'category': 'Breakfast', 'cost_per_portion': '40'})
        self.dosa.refresh_from_db()
        self.assertEqual(self.dosa.cost_per_portion, Decimal('40'))

    def test_deactivate_hides_item_from_record_form_but_keeps_history(self):
        make_record(self.dosa, 1, 10, 8)
        self.client.post(reverse('food_item_toggle', args=[self.dosa.pk]))
        self.dosa.refresh_from_db()
        self.assertFalse(self.dosa.is_active)
        form_items = list(self.client.get(reverse('add_record')).context['form'].fields['food_item'].queryset)
        self.assertEqual(form_items, [self.thali])
        self.assertEqual(FoodRecord.objects.count(), 1)

    def test_delete_blocked_when_records_exist_allowed_when_none(self):
        make_record(self.dosa, 1, 10, 8)
        self.client.post(reverse('food_item_delete', args=[self.dosa.pk]))
        self.assertTrue(FoodItem.objects.filter(pk=self.dosa.pk).exists())
        self.client.post(reverse('food_item_delete', args=[self.thali.pk]))
        self.assertFalse(FoodItem.objects.filter(pk=self.thali.pk).exists())

    def test_destructive_actions_need_post(self):
        self.assertEqual(self.client.get(reverse('food_item_delete', args=[self.dosa.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse('food_item_toggle', args=[self.dosa.pk])).status_code, 405)

    def test_pages_need_login(self):
        self.client.logout()
        for name, args in (('inventory', []), ('records', []), ('food_item_add', []), ('record_edit', [1])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302)


class RecordsPageTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.a = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=Decimal('60'))
        self.b = FoodItem.objects.create(name='Dosa', category='Breakfast', cost_per_portion=Decimal('35'))
        self.r1 = make_record(self.a, 1, 50, 40)
        self.r2 = make_record(self.b, 2, 20, 20)
        for d in range(3, 23):
            make_record(self.a, d, 10, 9)

    def test_summary_covers_all_filtered_rows_and_pagination(self):
        r = self.client.get(reverse('records'))
        self.assertEqual(len(r.context['page'].object_list), 15)
        self.assertEqual(r.context['page'].paginator.count, 22)
        s = r.context['summary']
        self.assertEqual(s['prepared'], 50 + 20 + 200)
        self.assertEqual(s['waste'], 10 + 0 + 20)

    def test_filters(self):
        r = self.client.get(reverse('records'), {'item': self.b.pk})
        self.assertEqual([x.pk for x in r.context['page'].object_list], [self.r2.pk])
        r = self.client.get(reverse('records'), {'q': 'dosa'})
        self.assertEqual(r.context['page'].paginator.count, 1)
        d = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.client.get(reverse('records'), {'start': d, 'end': d})
        self.assertEqual([x.pk for x in r.context['page'].object_list], [self.r1.pk])

    def test_garbage_filters_do_not_crash(self):
        r = self.client.get(reverse('records'), {'item': 'abc', 'start': 'zzz', 'page': 'x'})
        self.assertEqual(r.status_code, 200)

    def test_waste_cost_on_row(self):
        self.assertEqual(self.r1.waste_cost, Decimal('600'))

    def test_edit_record_keeps_validation_and_updates(self):
        url = reverse('record_edit', args=[self.r1.pk])
        base = {'food_item': self.a.pk, 'date': self.r1.date.isoformat(), 'prepared_quantity': 50, 'sold_quantity': 45, 'weather': 'Rainy'}
        r = self.client.post(url, {**base, 'sold_quantity': 60})
        self.assertContains(r, 'Sold quantity cannot be greater')
        self.client.post(url, base)
        self.r1.refresh_from_db()
        self.assertEqual((self.r1.sold_quantity, self.r1.weather), (45, 'Rainy'))

    def test_duplicate_item_and_date_rejected(self):
        r = self.client.post(reverse('add_record'), {'food_item': self.a.pk, 'date': self.r1.date.isoformat(),
                                                    'prepared_quantity': 5, 'sold_quantity': 5, 'weather': 'x'})
        self.assertContains(r, 'already exists')

    def test_delete_record_post_only_and_returns_to_filtered_list(self):
        url = reverse('record_delete', args=[self.r2.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url, {'next': reverse('records') + '?q=dosa'})
        self.assertRedirects(r, reverse('records') + '?q=dosa', fetch_redirect_response=False)
        self.assertFalse(FoodRecord.objects.filter(pk=self.r2.pk).exists())
        r = self.client.post(reverse('record_delete', args=[self.r1.pk]), {'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('records'), fetch_redirect_response=False)

    def test_empty_states(self):
        FoodRecord.objects.all().delete()
        self.assertContains(self.client.get(reverse('records')), 'No records yet')


# --- Phase 4: forecast engine and planner ------------------------------------
from datetime import date

from .models import Prediction
from .services import forecast as fc

MONDAY = date(2026, 11, 16)          # a Monday


def obs(d, sold, prepared=None, weather='', holiday=False, event=''):
    return fc.Obs(d, prepared if prepared is not None else sold + 5, sold, weather, holiday, event)


def daily_history(days=40, sold=50, rainy_sold=None, rainy_offsets=()):
    """One observation per day for `days` days before MONDAY, oldest first."""
    out = []
    for back in range(days, 0, -1):
        d = MONDAY - timedelta(days=back)
        rainy = back in rainy_offsets
        out.append(obs(d, rainy_sold if rainy else sold, weather='Rainy' if rainy else 'Sunny'))
    return out


class ForecastEngineTests(TestCase):
    def setUp(self):
        self.item = FoodItem(name='Thali', category='Main', cost_per_portion=60)

    def test_too_little_history_gives_no_forecast(self):
        f = fc.forecast_item(self.item, [obs(MONDAY - timedelta(days=7), 40)], MONDAY)
        self.assertFalse(f.ok)
        self.assertEqual(f.missing, 2)

    def test_weekday_baseline_reserve_and_recommendation(self):
        history = [obs(MONDAY - timedelta(days=7 * k), s) for k, s in zip((4, 3, 2, 1), (40, 50, 60, 50))]
        f = fc.forecast_item(self.item, history, MONDAY)
        self.assertEqual((f.method, f.expected, f.reserve, f.recommended), ('weekday', 50, 4, 54))
        self.assertEqual(len(f.sample), 4)
        self.assertIsNone(f.reliability)                       # only 4 days: no back-test
        self.assertFalse(f.factors)

    def test_falls_back_to_recent_average_when_weekday_data_is_thin(self):
        history = [obs(MONDAY - timedelta(days=k), s) for k, s in ((1, 30), (2, 40), (3, 50))]  # Sun, Sat, Fri
        f = fc.forecast_item(self.item, history, MONDAY)
        self.assertEqual((f.method, f.expected), ('recent', 40))
        self.assertTrue(any('weekday pattern is not captured' in n for n in f.notes))

    def test_weather_adjustment_learned_from_history(self):
        # Rainy non-Mondays sold 40 vs 50 otherwise -> ratio 0.8
        history = daily_history(rainy_sold=40, rainy_offsets=(2, 3, 4, 5))
        f = fc.forecast_item(self.item, history, MONDAY, weather='rainy')
        self.assertTrue(f.factors[0]['used'])
        self.assertAlmostEqual(f.ratio, 0.8)
        self.assertEqual(f.expected, 40)

    def test_weather_with_too_little_matching_history_is_not_applied(self):
        history = daily_history(rainy_sold=10, rainy_offsets=(2,))
        f = fc.forecast_item(self.item, history, MONDAY, weather='Rainy')
        self.assertFalse(f.factors[0]['used'])
        self.assertEqual(f.expected, 50)
        self.assertIn('no adjustment', f.notes[1])

    def test_factor_is_clamped(self):
        history = daily_history(rainy_sold=1, rainy_offsets=(2, 3, 4, 5))
        f = fc.forecast_item(self.item, history, MONDAY, weather='Rainy')
        self.assertAlmostEqual(f.ratio, 0.7)

    def test_unknown_event_makes_no_adjustment_and_says_so(self):
        f = fc.forecast_item(self.item, daily_history(), MONDAY, event='Cricket final')
        self.assertEqual(f.expected, 50)
        self.assertFalse(f.factors[0]['used'])

    def test_reliability_backtest_needs_five_points_and_reports_error(self):
        f = fc.forecast_item(self.item, daily_history(), MONDAY)
        self.assertEqual(f.reliability['n'], 10)
        self.assertEqual((f.reliability['pct'], f.reliability['level']), (0, 'High'))
        noisy = [obs(MONDAY - timedelta(days=k), 20 if k % 2 else 80) for k in range(40, 0, -1)]
        self.assertEqual(fc.forecast_item(self.item, noisy, MONDAY).reliability['level'], 'Low')

    def test_sell_out_warning(self):
        history = [fc.Obs(MONDAY - timedelta(days=7 * k), 50, 50, '', False, '') for k in (1, 2, 3, 4)]
        f = fc.forecast_item(self.item, history, MONDAY)
        self.assertTrue(f.warnings)

    def test_untested_inputs_are_declared(self):
        f = fc.forecast_item(self.item, daily_history(), MONDAY)
        joined = ' '.join(f.not_used)
        self.assertIn('Traffic', joined)
        self.assertIn('Time-of-day', joined)

    def test_history_never_includes_target_day_or_later(self):
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        for delta in (-2, -1, 0, 1):
            FoodRecord.objects.create(food_item=item, date=MONDAY + timedelta(days=delta), prepared_quantity=10, sold_quantity=9, weather='x')
        got = fc.load_observations([item], MONDAY)[item.pk]
        self.assertEqual([o.date for o in got], [MONDAY - timedelta(days=2), MONDAY - timedelta(days=1)])


class PlannerWorkflowTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)
        self.target = timezone.localdate() + timedelta(days=1)
        for k in range(1, 31):
            FoodRecord.objects.create(food_item=self.item, date=timezone.localdate() - timedelta(days=k),
                                      prepared_quantity=55, sold_quantity=50, weather='Sunny')
        self.empty_item = FoodItem.objects.create(name='Soup', category='Starter', cost_per_portion=20)

    def params(self, **kw):
        return {'date': self.target.isoformat(), 'item': 'all', **kw}

    def test_forecast_page_renders_results_and_missing_data(self):
        r = self.client.get(reverse('forecast'), self.params(generate='1'))
        self.assertEqual(r.status_code, 200)
        names = {f.item.name: f for f in r.context['results']}
        self.assertTrue(names['Thali'].ok)
        self.assertFalse(names['Soup'].ok)
        self.assertContains(r, 'Not enough history')

    def test_forecast_single_item_shows_hero(self):
        r = self.client.get(reverse('forecast'), self.params(generate='1', item=self.item.pk))
        self.assertIsNotNone(r.context['single'])
        self.assertContains(r, 'How this was calculated')

    def test_bad_inputs_do_not_crash(self):
        self.assertEqual(self.client.get(reverse('forecast'), {'generate': '1', 'date': 'nope', 'item': 'xyz'}).status_code, 200)

    def test_save_creates_plan_lines_server_side_and_skips_no_data(self):
        r = self.client.post(reverse('forecast_save'), self.params())
        self.assertRedirects(r, f"{reverse('planner')}?date={self.target.isoformat()}", fetch_redirect_response=False)
        p = Prediction.objects.get()
        self.assertEqual((p.food_item, p.predicted_demand, p.status), (self.item, 50, 'suggested'))
        self.assertIn('Baseline', p.explanation)

    def test_resaving_overwrites_suggestions_but_not_approved_lines(self):
        self.client.post(reverse('forecast_save'), self.params())
        p = Prediction.objects.get()
        self.client.post(reverse('planner_action', args=[p.pk]), {'action': 'approve', 'quantity': '70'})
        self.client.post(reverse('forecast_save'), self.params())
        p.refresh_from_db()
        self.assertEqual((p.status, p.approved_quantity), ('approved', 70))
        self.assertEqual(Prediction.objects.count(), 1)

    def test_approval_flow_records_who_and_when(self):
        self.client.post(reverse('forecast_save'), self.params())
        p = Prediction.objects.get()
        url = reverse('planner_action', args=[p.pk])
        self.client.post(url, {'action': 'prepared'})                    # not approved yet
        p.refresh_from_db(); self.assertEqual(p.status, 'suggested')
        self.client.post(url, {'action': 'approve', 'quantity': 'abc'})  # invalid
        p.refresh_from_db(); self.assertEqual(p.status, 'suggested')
        self.client.post(url, {'action': 'approve', 'quantity': '58'})
        p.refresh_from_db()
        self.assertEqual((p.status, p.quantity_to_prepare, p.approved_by), ('approved', 58, self.user))
        self.assertIsNotNone(p.approved_at)
        self.client.post(url, {'action': 'prepared'})
        p.refresh_from_db(); self.assertEqual(p.status, 'prepared')
        self.client.post(url, {'action': 'reopen'})
        p.refresh_from_db()
        self.assertEqual((p.status, p.approved_quantity, p.approved_by), ('suggested', None, None))

    def test_approve_all_uses_recommended_quantities(self):
        self.client.post(reverse('forecast_save'), self.params())
        self.client.post(reverse('planner_approve_all'), {'date': self.target.isoformat()})
        p = Prediction.objects.get()
        self.assertEqual((p.status, p.approved_quantity), ('approved', p.recommended_preparation))

    def test_planner_page_and_empty_state(self):
        r = self.client.get(reverse('planner'), {'date': self.target.isoformat()})
        self.assertContains(r, 'No plan for this day yet')
        self.client.post(reverse('forecast_save'), self.params())
        r = self.client.get(reverse('planner'))                    # defaults to the next planned date
        self.assertEqual(r.context['date'], self.target)
        self.assertContains(r, 'Approve all')

    def test_actions_require_post_and_login(self):
        self.assertEqual(self.client.get(reverse('forecast_save')).status_code, 405)
        self.assertEqual(self.client.get(reverse('planner_approve_all')).status_code, 405)
        self.client.logout()
        for name in ('forecast', 'planner'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_add_record_prefill_from_planner(self):
        r = self.client.get(reverse('add_record'), {'item': self.item.pk, 'date': self.target.isoformat(), 'prepared': 58})
        form = r.context['form']
        self.assertEqual(form.initial['food_item'], self.item.pk)
        self.assertEqual(form.initial['prepared_quantity'], 58)


# --- Profile, sign-up wizard, settings -----------------------------------------
from django.core import mail as _mail  # noqa: E402

from .models import RestaurantSettings, TrafficObservation, UserProfile  # noqa: E402


class SignUpWizardTests(TestCase):
    base = {'full_name': 'Asha Verma', 'email': 'asha@example.com',
            'password1': 'Sturdy-pass-928', 'password2': 'Sturdy-pass-928'}

    def test_public_signup_can_never_choose_a_role_or_become_owner(self):
        for forged in ('owner', 'manager'):
            User.objects.all().delete()
            self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA, 'role': forged,
                                                 'is_superuser': 'on', 'is_staff': 'on'})
            u = User.objects.get(email='asha@example.com')
            self.assertEqual(u.profile.role, 'staff')
            self.assertFalse(u.is_superuser or u.is_staff or u.is_active)

    def test_signup_does_not_touch_restaurant_settings(self):
        before = RestaurantSettings.load().name
        self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA, 'restaurant_name': 'Hijack'})
        self.assertEqual(RestaurantSettings.load().name, before)

    def test_terms_are_required(self):
        r = self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA, 'accept_terms': ''})
        self.assertContains(r, 'accept the terms')
        self.assertFalse(User.objects.exists())

    def test_pending_user_gets_a_clear_message_on_login(self):
        self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA})
        r = self.client.post(reverse('login'), {'identifier': 'asha@example.com', 'password': 'Sturdy-pass-928'})
        self.assertContains(r, 'waiting for the owner')
        r = self.client.post(reverse('login'), {'identifier': 'asha@example.com', 'password': 'wrong-pass'})
        self.assertNotContains(r, 'waiting for the owner')   # no account probing

    def test_wizard_markup_and_error_step(self):
        r = self.client.get(reverse('signup'))
        for marker in ('data-wizard', 'data-step="2"', 'data-suggest', 'data-rule="len"'):
            self.assertContains(r, marker)
        r = self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA, 'accept_terms': ''})
        self.assertContains(r, 'data-error-step="2"')
        r = self.client.post(reverse('signup'), {**self.base, **SIGNUP_EXTRA, 'password2': 'x'})
        self.assertContains(r, 'data-error-step="1"')


class SettingsPageTests(LoggedInTestCase):
    def test_profile_update_ignores_a_forged_role(self):
        r = self.client.post(reverse('settings'), {'form': 'profile', 'first_name': 'Ravi', 'last_name': 'K',
                                                   'email': 'ravi@example.com', 'role': 'manager', 'phone': '+91 98765 43210'})
        self.assertRedirects(r, f"{reverse('settings')}?tab=profile")
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.email), ('Ravi', 'ravi@example.com'))
        self.assertEqual(UserProfile.for_user(self.user).role, 'owner')     # unchanged
        self.assertContains(self.client.get(reverse('dashboard')), 'Owner')

    def test_profile_rejects_duplicate_email_and_bad_phone(self):
        User.objects.create_user('other', 'taken@example.com', 'pw-12345-xyz')
        r = self.client.post(reverse('settings'), {'form': 'profile', 'first_name': 'A', 'email': 'TAKEN@example.com', 'role': 'owner', 'phone': ''})
        self.assertContains(r, 'already uses this email')
        r = self.client.post(reverse('settings'), {'form': 'profile', 'first_name': 'A', 'email': 'a@example.com', 'role': 'owner', 'phone': 'abc'})
        self.assertContains(r, 'valid phone')

    def test_restaurant_settings_flow_through_the_app(self):
        data = {'form': 'restaurant', 'name': 'Highway Spice', 'business_type': 'dhaba', 'city': 'Vapi', 'address': 'NH48',
                'phone': '', 'latitude': '20.37', 'longitude': '72.91', 'currency_symbol': '$', 'waste_alert_threshold': '15'}
        self.client.post(reverse('settings'), data)
        r = RestaurantSettings.load()
        self.assertEqual((r.name, r.currency_symbol, r.waste_alert_threshold, r.is_configured), ('Highway Spice', '$', 15, True))
        item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=10)
        make_record(item, 1, 20, 10)
        page = self.client.get(reverse('dashboard'), {'range': '7'})
        self.assertContains(page, 'Highway Spice')
        self.assertContains(page, '$100')               # 10 wasted x 10 cost

    def test_restaurant_validation(self):
        base = {'form': 'restaurant', 'name': 'X', 'business_type': 'cafe', 'currency_symbol': '₹', 'waste_alert_threshold': '20'}
        self.assertContains(self.client.post(reverse('settings'), {**base, 'latitude': '12'}), 'both latitude and longitude')
        self.assertContains(self.client.post(reverse('settings'), {**base, 'latitude': '95', 'longitude': '10'}), 'between -90 and 90')
        self.assertContains(self.client.post(reverse('settings'), {**base, 'waste_alert_threshold': '0'}), 'between 1 and 100')

    def test_password_change_keeps_session(self):
        r = self.client.post(reverse('settings'), {'form': 'password', 'old_password': 'pw-12345-xyz',
                                                   'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77'})
        self.assertRedirects(r, f"{reverse('settings')}?tab=password")
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.client.logout()
        self.assertTrue(self.client.login(username='owner', password='Brand-new-pass-77'))

    def test_wrong_current_password_is_rejected(self):
        r = self.client.post(reverse('settings'), {'form': 'password', 'old_password': 'nope',
                                                   'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77'})
        self.assertContains(r, 'field-error')

    def test_account_without_password_gets_explanation_not_password_form(self):
        self.user.set_unusable_password(); self.user.save()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('settings')), 'no password')


# --- Analytics and reports -------------------------------------------------------

class AnalyticsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.a = FoodItem.objects.create(name='Biryani', category='Main', cost_per_portion=Decimal('80'))
        self.b = FoodItem.objects.create(name='Coffee', category='Drinks', cost_per_portion=Decimal('10'))
        for d in range(1, 15):                                   # biryani: prepares 50, sells 30-ish (heavy waste)
            make_record(self.a, d, 50, 30 + d % 3)
            make_record(self.b, d, 40, 39)

    def test_totals_categories_and_item_ranking(self):
        ctx = self.client.get(reverse('analytics'), {'range': '30'}).context
        t = ctx['totals']
        self.assertEqual(t['prepared'], 14 * 90)
        self.assertEqual([c['name'] for c in ctx['categories']], ['Main', 'Drinks'])
        self.assertEqual(ctx['items_by_cost'][0]['name'], 'Biryani')
        self.assertEqual(sum(c['share'] for c in ctx['categories']), 100)

    def test_item_suggestion_has_numbers_and_tradeoff(self):
        ctx = self.client.get(reverse('analytics'), {'range': '30'}).context
        top = ctx['suggestions'][0]
        self.assertIn('Biryani', top['title'])
        self.assertIn('run out', top['body'])

    def test_no_suggestion_below_seven_records(self):
        FoodRecord.objects.filter(food_item=self.a).delete()
        for d in range(1, 5):
            make_record(self.a, d + 20, 50, 20)
        titles = ' '.join(s['title'] for s in self.client.get(reverse('analytics'), {'range': 'all'}).context['suggestions'])
        self.assertNotIn('Biryani', titles)

    def test_empty_states_and_trend_buckets(self):
        self.assertContains(self.client.get(reverse('analytics'), {'range': '7', 'start': '2000-01-01', 'end': '2000-01-02'}), 'No records in this date range')
        FoodRecord.objects.all().delete()
        self.assertContains(self.client.get(reverse('analytics')), 'No data to analyse yet')

    def test_long_ranges_use_weekly_buckets(self):
        make_record(self.a, 100, 50, 40)
        ctx = self.client.get(reverse('analytics'), {'range': 'all'}).context
        self.assertEqual(ctx['trend']['unit'], 'week')

    def test_page_renders_with_charts(self):
        r = self.client.get(reverse('analytics'), {'range': '30'})
        self.assertContains(r, 'trend-data')
        self.assertContains(r, 'How to waste less')


class ReportsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=Decimal('60'))
        self.today = timezone.localdate()

    def test_period_bounds(self):
        from .services.reports import period_bounds
        mon = date(2026, 11, 16)
        kind, start, end, *_ = period_bounds('week', {'date': '2026-11-19'}, mon)
        self.assertEqual((start, end), (date(2026, 11, 16), date(2026, 11, 22)))
        kind, start, end, label, prev, nxt = period_bounds('month', {'month': '2026-02'}, mon)
        self.assertEqual((start, end, label), (date(2026, 2, 1), date(2026, 2, 28), 'February 2026'))
        self.assertEqual((prev, nxt), ('month=2026-01', 'month=2026-03'))
        self.assertEqual(period_bounds('bogus', {}, mon)[0], 'week')

    def test_daily_weekly_monthly_pages(self):
        make_record(self.item, 1, 50, 40)
        for kind in ('day', 'week', 'month'):
            r = self.client.get(reverse('reports'), {'kind': kind, 'date': (self.today - timedelta(days=1)).isoformat(),
                                                      'month': f'{self.today - timedelta(days=1):%Y-%m}'})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.context['has_records'], kind)
            self.assertEqual(r.context['totals']['waste'], 10)

    def test_forecast_vs_actual_joins_saved_plan_with_real_sales(self):
        day = self.today - timedelta(days=1)
        Prediction.objects.create(food_item=self.item, date=day, predicted_demand=45, recommended_preparation=50, safety_reserve=5)
        r = self.client.get(reverse('reports'), {'kind': 'day', 'date': day.isoformat()})
        self.assertEqual(r.context['fva'], [])                   # no actual yet
        make_record(self.item, 1, 50, 40)
        ctx = self.client.get(reverse('reports'), {'kind': 'day', 'date': day.isoformat()}).context
        row = ctx['fva'][0]
        self.assertEqual((row['predicted'], row['actual'], row['error'], row['error_pct']), (45, 40, -5, 12.5))
        self.assertEqual(ctx['fva_summary']['mae'], 5.0)

    def test_csv_exports(self):
        make_record(self.item, 1, 50, 40)
        day = (self.today - timedelta(days=1)).isoformat()
        r = self.client.get(reverse('reports_export'), {'kind': 'day', 'date': day, 'type': 'summary'})
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment', r['Content-Disposition'])
        body = r.content.decode('utf-8-sig')
        self.assertIn('Thali,Main,50,40,10,20.0,600.00', body)
        self.assertIn('TOTAL', body)
        rec = self.client.get(reverse('reports_export'), {'kind': 'day', 'date': day, 'type': 'records'}).content.decode('utf-8-sig')
        self.assertIn(f'{day},Thali,Main,50,40,10,20.0,600.00', rec)
        self.assertEqual(self.client.get(reverse('reports_export'), {'type': 'nope'}).status_code, 404)

    def test_csv_blocks_formula_injection(self):
        from .services.reports import safe_cell
        self.assertEqual(safe_cell('=HYPERLINK("x")'), "'=HYPERLINK(\"x\")")
        self.assertEqual(safe_cell('+1'), "'+1")
        self.assertEqual(safe_cell('Thali'), 'Thali')
        self.assertEqual(safe_cell(5), 5)

    def test_empty_period(self):
        r = self.client.get(reverse('reports'), {'kind': 'day', 'date': '2001-01-01'})
        self.assertContains(r, 'No records in this period')


# --- Traffic ------------------------------------------------------------------------

class TrafficServiceTests(TestCase):
    def test_classification_thresholds(self):
        from .services.traffic import classify, TrafficError
        self.assertEqual([classify(c, 100) for c in (100, 80, 79, 50, 49, 25, 24, 0)],
                         ['free', 'free', 'moderate', 'moderate', 'heavy', 'heavy', 'standstill', 'standstill'])
        with self.assertRaises(TrafficError):
            classify(10, 0)

    def test_fetch_live_parses_provider_response_and_reports_failures(self):
        import io, json, urllib.error
        from .services.traffic import fetch_live, TrafficError

        def good(url, timeout):
            self.assertIn('point=20.37%2C72.91', url)
            return io.BytesIO(json.dumps({'flowSegmentData': {'currentSpeed': 30, 'freeFlowSpeed': 60, 'confidence': 0.9}}).encode())
        r = fetch_live(20.37, 72.91, api_key='k', opener=good)
        self.assertEqual((r['condition'], r['current_speed']), ('moderate', Decimal('30')))

        def forbidden(url, timeout):
            raise urllib.error.HTTPError(url, 403, 'no', {}, None)
        with self.assertRaisesMessage(TrafficError, 'API key'):
            fetch_live(1, 1, api_key='k', opener=forbidden)

        def offline(url, timeout):
            raise urllib.error.URLError('down')
        with self.assertRaisesMessage(TrafficError, 'Could not reach'):
            fetch_live(1, 1, api_key='k', opener=offline)
        with self.assertRaisesMessage(TrafficError, 'No traffic API key'):
            fetch_live(1, 1, api_key='')

    def test_daily_conditions_keeps_latest_reading_per_day(self):
        now = timezone.now().replace(hour=12, minute=0)
        TrafficObservation.objects.create(observed_at=now - timedelta(hours=3), condition='heavy')
        TrafficObservation.objects.create(observed_at=now, condition='free')
        from .services.traffic import daily_conditions
        self.assertEqual(list(daily_conditions().values()), ['free'])


class TrafficPageTests(LoggedInTestCase):
    def test_page_without_location_or_api_says_unavailable_and_still_works(self):
        r = self.client.get(reverse('traffic'))
        self.assertContains(r, 'No location set')
        self.assertContains(r, 'Live traffic is not connected')
        self.assertContains(r, 'Traffic information is unavailable')
        self.assertNotContains(r, 'Fetch live reading')

    def test_condition_choices_have_no_blank_option(self):
        form = self.client.get(reverse('traffic')).context['form']
        self.assertEqual([v for v, _ in form.fields['condition'].choices], ['free', 'moderate', 'heavy', 'standstill'])
        self.assertNotContains(self.client.get(reverse('traffic')), 'Select an option')

    def test_map_shown_only_with_location(self):
        r = RestaurantSettings.load(); r.latitude, r.longitude = Decimal('20.37'), Decimal('72.91'); r.save()
        self.assertContains(self.client.get(reverse('traffic')), 'id="map"')

    def test_manual_entry_and_validation(self):
        when = timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('traffic_add'), {'condition': 'heavy', 'observed_at': when, 'notes': 'Toll queue'})
        o = TrafficObservation.objects.get()
        self.assertEqual((o.condition, o.source, o.created_by), ('heavy', 'Manual entry', self.user))
        future = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        r = self.client.post(reverse('traffic_add'), {'condition': 'free', 'observed_at': future}, follow=True)
        self.assertContains(r, 'now or the past')
        self.assertEqual(TrafficObservation.objects.count(), 1)
        page = self.client.get(reverse('traffic'))
        self.assertContains(page, 'Toll queue')

    def test_fetch_without_provider_is_refused_politely_and_creates_nothing(self):
        r = self.client.post(reverse('traffic_fetch'), follow=True)
        self.assertContains(r, 'No traffic provider is configured')
        self.assertEqual(TrafficObservation.objects.count(), 0)

    def test_fetch_with_mocked_provider_saves_a_labelled_reading(self):
        from unittest import mock
        from django.test import override_settings
        r = RestaurantSettings.load(); r.latitude, r.longitude = Decimal('20.37'), Decimal('72.91'); r.save()
        reading = {'current_speed': Decimal('20'), 'free_flow_speed': Decimal('80'), 'condition': 'heavy', 'confidence': 1}
        with override_settings(TRAFFIC_ENABLED=True, TRAFFIC_API_KEY='k'), \
                mock.patch('predictor.services.traffic.fetch_live', return_value=reading):
            self.assertContains(self.client.get(reverse('traffic')), 'Fetch live reading')
            self.client.post(reverse('traffic_fetch'))
        o = TrafficObservation.objects.get()
        self.assertEqual((o.condition, o.source), ('heavy', 'TomTom Traffic Flow'))

    def test_delete_requires_post(self):
        o = TrafficObservation.objects.create(observed_at=timezone.now(), condition='free')
        self.assertEqual(self.client.get(reverse('traffic_delete', args=[o.pk])).status_code, 405)
        self.client.post(reverse('traffic_delete', args=[o.pk]))
        self.assertEqual(TrafficObservation.objects.count(), 0)


class ForecastTrafficFactorTests(TestCase):
    def setUp(self):
        self.item = FoodItem(name='Thali', category='Main', cost_per_portion=60)

    def history(self, heavy_sold):
        out = []
        for back in range(40, 0, -1):
            d = MONDAY - timedelta(days=back)
            heavy = back % 4 == 0
            out.append(fc.Obs(d, 60, heavy_sold if heavy else 50, 'Sunny', False, '', 'heavy' if heavy else 'free'))
        return out

    def test_traffic_adjustment_learned_from_recorded_days(self):
        f = fc.forecast_item(self.item, self.history(40), MONDAY, traffic='heavy')
        self.assertTrue(f.factors[0]['used'])
        self.assertAlmostEqual(f.factors[0]['ratio'], 0.8)
        self.assertEqual(f.expected, round(f.baseline * 0.8))
        self.assertNotIn('Traffic (no expected traffic was entered)', f.not_used)

    def test_no_traffic_history_means_no_adjustment_and_says_so(self):
        f = fc.forecast_item(self.item, daily_history(), MONDAY, traffic='heavy')
        self.assertFalse(f.factors[0]['used'])
        self.assertIn('no traffic has been recorded', f.factors[0]['note'])
        self.assertEqual(f.expected, 50)

    def test_traffic_not_entered_is_declared_unused(self):
        f = fc.forecast_item(self.item, self.history(40), MONDAY)
        self.assertFalse(f.factors)
        self.assertIn('Traffic', ' '.join(f.not_used))

    def test_forecast_page_accepts_traffic_param(self):
        user = User.objects.create_user('u', 'u@example.com', 'pw-12345-xyz')
        UserProfile.objects.update_or_create(user=user, defaults={'role': 'manager'}); self.client.force_login(user)
        r = self.client.get(reverse('forecast'), {'generate': '1', 'traffic': 'heavy', 'date': '2026-11-16'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['inputs']['traffic'], 'heavy')
        r = self.client.get(reverse('forecast'), {'generate': '1', 'traffic': 'bogus'})
        self.assertEqual(r.context['inputs']['traffic'], '')


class PolishTests(TestCase):
    def test_seed_command_creates_data_once_and_refuses_to_overwrite(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        call_command('seed_demo_data', '--days', '10', stdout=StringIO())
        self.assertEqual(FoodItem.objects.count(), 5)
        self.assertEqual(FoodRecord.objects.count(), 50)
        with self.assertRaises(CommandError):
            call_command('seed_demo_data', stdout=StringIO())
        call_command('seed_demo_data', '--days', '10', '--force', stdout=StringIO())
        self.assertEqual(FoodRecord.objects.count(), 50)          # idempotent: nothing duplicated

    def test_custom_404_page(self):
        r = self.client.get('/definitely/not/a/page/')
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'Page not found', status_code=404)

    def test_every_sidebar_link_resolves(self):
        user = User.objects.create_user('u', 'u@example.com', 'pw-12345-xyz'); self.client.force_login(user)
        r = self.client.get(reverse('dashboard'))
        for item in r.context['nav_items']:
            self.assertIsNotNone(item['url_name'], item['label'])
            self.assertEqual(self.client.get(reverse(item['url_name'])).status_code, 200, item['label'])


class DateFormattingTests(LoggedInTestCase):
    """Phase B: DD-MM-YYYY display, ISO storage, year boundary, IST handling."""

    def setUp(self):
        super().setUp()
        self.item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)

    def test_format_module_is_active(self):
        from django.utils import formats
        import datetime
        d = datetime.date(2026, 12, 31)
        self.assertEqual(formats.date_format(d, 'DATE_FORMAT'), '31-12-2026')
        self.assertEqual(formats.date_format(d, 'SHORT_DATE_FORMAT'), '31-12-2026')
        # first input format stays ISO so date widgets render a value pickers understand
        self.assertEqual(formats.get_format('DATE_INPUT_FORMATS')[0], '%Y-%m-%d')

    def test_year_boundary_saves_and_displays(self):
        import datetime
        for d, shown in [(datetime.date(2026, 12, 31), '31-12-2026'), (datetime.date(2027, 1, 1), '01-01-2027')]:
            r = FoodRecord.objects.create(food_item=self.item, date=d, prepared_quantity=10, sold_quantity=8)
            r.refresh_from_db()
            self.assertIsInstance(r.date, datetime.date)       # a real date, not a string
            self.assertEqual(r.date, d)
            page = self.client.get(reverse('records'), {'start': '2026-12-01', 'end': '2027-01-31'})
            self.assertContains(page, shown)

    def test_form_accepts_iso_and_ddmmyyyy(self):
        from .forms import FoodRecordForm
        base = {'food_item': self.item.pk, 'prepared_quantity': 10, 'sold_quantity': 5, 'weather': 'Sunny'}
        for text in ('2026-03-01', '01-03-2026', '01/03/2026'):
            f = FoodRecordForm({**base, 'date': text})
            self.assertTrue(f.is_valid(), (text, f.errors))
            import datetime
            self.assertEqual(f.cleaned_data['date'], datetime.date(2026, 3, 1))

    def test_rejects_six_digit_and_ancient_years(self):
        from .forms import FoodRecordForm
        base = {'food_item': self.item.pk, 'prepared_quantity': 10, 'sold_quantity': 5, 'weather': 'Sunny'}
        self.assertFalse(FoodRecordForm({**base, 'date': '202026-03-01'}).is_valid())
        self.assertFalse(FoodRecordForm({**base, 'date': '1999-03-01'}).is_valid())

    def test_future_dates_rejected(self):
        from .forms import FoodRecordForm
        future = timezone.localdate() + timedelta(days=2)
        f = FoodRecordForm({'food_item': self.item.pk, 'prepared_quantity': 10, 'sold_quantity': 5,
                            'weather': 'Sunny', 'date': future.isoformat()})
        self.assertFalse(f.is_valid())
        self.assertIn('date', f.errors)

    def test_edit_form_prefills_iso_value_for_picker(self):
        import datetime
        r = FoodRecord.objects.create(food_item=self.item, date=datetime.date(2027, 1, 1),
                                      prepared_quantity=10, sold_quantity=8)
        page = self.client.get(reverse('record_edit', args=[r.pk]))
        self.assertContains(page, 'value="2027-01-01"')

    def test_weekly_report_label_across_year_boundary(self):
        import datetime
        from .services.reports import period_bounds
        kind, start, end, label, _, _ = period_bounds('week', {'date': '2027-01-01'}, datetime.date(2027, 1, 5))
        self.assertEqual((start, end), (datetime.date(2026, 12, 28), datetime.date(2027, 1, 3)))
        self.assertEqual(label, '28-12-2026 – 03-01-2027')

    def test_today_follows_india_time_not_utc(self):
        import datetime
        from unittest import mock
        # 20:00 UTC on 31 Dec is already 01:30 on 1 Jan in Asia/Kolkata
        utc = datetime.datetime(2026, 12, 31, 20, 0, tzinfo=datetime.timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=utc):
            self.assertEqual(timezone.localdate(), datetime.date(2027, 1, 1))


# ---------------------------------------------------------------------------------------
# Roles, permissions, approval workflow, user management, activity log
# ---------------------------------------------------------------------------------------
from django.contrib.auth.models import Group  # noqa: E402

from .access import sync_roles  # noqa: E402
from .models import ActivityLog  # noqa: E402

PW = 'pw-12345-xyz'


def make_user(name, role, **extra):
    user = User.objects.create_user(name, f'{name}@example.com', PW, first_name=name.title(), **extra)
    UserProfile.objects.update_or_create(user=user, defaults={'role': role})
    return user


class RoleTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user('olive', 'owner')
        self.manager = make_user('manu', 'manager')
        self.staff = make_user('sam', 'staff')
        self.other_staff = make_user('sana', 'staff')
        self.item = FoodItem.objects.create(name='Thali', category='Main', cost_per_portion=60)

    def as_user(self, user):
        self.client.force_login(user)
        return self.client

    def record(self, by=None, status='approved', days_ago=1, **kw):
        return FoodRecord.objects.create(
            food_item=self.item, date=timezone.localdate() - timedelta(days=days_ago),
            prepared_quantity=10, sold_quantity=7, weather='Sunny', created_by=by, status=status, **kw)


class RoleSystemTests(RoleTestCase):
    def test_groups_exist_with_expected_permissions(self):
        self.assertEqual(set(Group.objects.values_list('name', flat=True)), {'Owner', 'Manager', 'Staff'})
        names = lambda g: set(Group.objects.get(name=g).permissions.values_list('codename', flat=True))
        self.assertIn('manage_users', names('Owner'))
        self.assertNotIn('manage_users', names('Manager'))
        self.assertNotIn('change_restaurantsettings', names('Manager'))
        self.assertIn('approve_foodrecord', names('Manager'))
        self.assertNotIn('delete_foodrecord', names('Staff'))
        self.assertNotIn('view_analytics', names('Staff'))
        self.assertNotIn('change_fooditem', names('Staff'))

    def test_users_are_placed_in_the_group_of_their_role(self):
        for user, group in ((self.owner, 'Owner'), (self.manager, 'Manager'), (self.staff, 'Staff')):
            self.assertEqual([g.name for g in user.groups.all()], [group])

    def test_sync_is_idempotent_and_repairs_drift(self):
        self.staff.groups.clear()
        Group.objects.get(name='Staff').permissions.clear()
        sync_roles(); sync_roles()
        self.assertEqual([g.name for g in self.staff.groups.all()], ['Staff'])
        self.assertTrue(Group.objects.get(name='Staff').permissions.exists())

    def test_default_role_is_least_privilege(self):
        u = User.objects.create_user('nobody', 'n@example.com', PW)
        self.assertEqual(UserProfile.for_user(u).role, 'staff')

    def test_changing_the_role_moves_the_group(self):
        p = self.staff.profile; p.role = 'manager'; p.save()
        self.assertEqual([g.name for g in self.staff.groups.all()], ['Manager'])

    def test_superuser_counts_as_owner(self):
        from .access import role_of
        su = User.objects.create_superuser('root', 'root@example.com', PW)
        self.assertEqual(role_of(su), 'owner')

    def test_nobody_but_superuser_reaches_django_admin(self):
        for u in (self.owner, self.manager, self.staff):
            self.assertFalse(u.is_staff or u.is_superuser)


ACCESS_MATRIX = {
    # url name: (owner, manager, staff) expected status codes for GET
    'dashboard': (200, 200, 200), 'inventory': (200, 200, 200), 'records': (200, 200, 200),
    'add_record': (200, 200, 200), 'planner': (200, 200, 200), 'notifications': (200, 200, 200),
    'settings': (200, 200, 200),
    'forecast': (200, 200, 403), 'analytics': (200, 200, 403), 'reports': (200, 200, 403),
    'traffic': (200, 200, 403), 'approvals': (200, 200, 403), 'food_item_add': (200, 200, 403),
    'user_list': (200, 403, 403), 'user_add': (200, 403, 403), 'activity_log': (200, 403, 403),
}


class AccessMatrixTests(RoleTestCase):
    def test_get_access_by_role(self):
        for name, expected in ACCESS_MATRIX.items():
            for user, want in zip((self.owner, self.manager, self.staff), expected):
                r = self.as_user(user).get(reverse(name))
                self.assertEqual(r.status_code, want, f'{name} as {user.username}')

    def test_anonymous_is_sent_to_login_everywhere(self):
        self.client.logout()
        for name in ACCESS_MATRIX:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('/login/', r['Location'])

    def test_forbidden_page_uses_the_friendly_message(self):
        r = self.as_user(self.staff).get(reverse('analytics'))
        self.assertContains(r, 'You do not have permission to perform this action.', status_code=403)

    def test_reports_csv_export_is_not_reachable_by_staff(self):
        self.assertEqual(self.as_user(self.staff).get(reverse('reports_export'), {'type': 'records'}).status_code, 403)
        self.assertEqual(self.as_user(self.manager).get(reverse('reports_export'), {'type': 'records'}).status_code, 200)

    def test_manager_cannot_change_restaurant_settings(self):
        data = {'form': 'restaurant', 'name': 'Hacked', 'business_type': 'dhaba', 'city': 'X', 'address': '',
                'phone': '', 'latitude': '', 'longitude': '', 'currency_symbol': '₹', 'waste_alert_threshold': '10'}
        before = RestaurantSettings.load().name
        self.assertEqual(self.as_user(self.manager).post(reverse('settings'), data).status_code, 403)
        self.assertEqual(RestaurantSettings.load().name, before)
        self.assertNotContains(self.as_user(self.manager).get(reverse('settings')), 'data-tab="restaurant"')
        self.as_user(self.owner).post(reverse('settings'), data)
        self.assertEqual(RestaurantSettings.load().name, 'Hacked')

    def test_menu_hides_what_the_role_cannot_use(self):
        staff_page = self.as_user(self.staff).get(reverse('dashboard'))
        for hidden in ('Waste Analytics', 'Reports', 'Team', 'Activity Log', 'Demand Forecast'):
            self.assertNotContains(staff_page, f'<span>{hidden}</span>')
        owner_page = self.as_user(self.owner).get(reverse('dashboard'))
        for shown in ('Team', 'Activity Log', 'Reports'):
            self.assertContains(owner_page, f'<span>{shown}</span>')

    def test_staff_dashboard_is_the_simplified_one(self):
        self.assertTemplateUsed(self.as_user(self.staff).get(reverse('dashboard')), 'predictor/staff_dashboard.html')
        self.assertTemplateNotUsed(self.as_user(self.manager).get(reverse('dashboard')), 'predictor/staff_dashboard.html')

    def test_staff_does_not_see_prices_or_waste_cost(self):
        self.record(by=self.staff)
        self.assertNotContains(self.as_user(self.staff).get(reverse('inventory')), '60.00')
        self.assertNotContains(self.as_user(self.staff).get(reverse('records')), 'Waste cost')
        self.assertContains(self.as_user(self.manager).get(reverse('inventory')), '60.00')


class StaffRestrictionTests(RoleTestCase):
    def test_staff_cannot_delete_records(self):
        rec = self.record(by=self.staff, status='draft')
        r = self.as_user(self.staff).post(reverse('record_delete', args=[rec.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(FoodRecord.objects.filter(pk=rec.pk).exists())

    def test_staff_cannot_edit_another_staff_members_record(self):
        rec = self.record(by=self.other_staff, status='draft')
        c = self.as_user(self.staff)
        self.assertEqual(c.get(reverse('record_edit', args=[rec.pk])).status_code, 403)
        data = {'food_item': self.item.pk, 'date': rec.date.isoformat(), 'prepared_quantity': 99,
                'sold_quantity': 1, 'weather': 'Hacked', 'submit_action': 'submit'}
        self.assertEqual(c.post(reverse('record_edit', args=[rec.pk]), data).status_code, 403)
        rec.refresh_from_db()
        self.assertEqual((rec.prepared_quantity, rec.status), (10, 'draft'))

    def test_staff_only_sees_their_own_records(self):
        mine = self.record(by=self.staff, days_ago=1)
        theirs = self.record(by=self.other_staff, days_ago=2)
        legacy = self.record(by=None, days_ago=3)
        r = self.as_user(self.staff).get(reverse('records'))
        ids = [x.pk for x in r.context['page'].object_list]
        self.assertEqual(ids, [mine.pk])
        self.assertEqual(len(self.as_user(self.manager).get(reverse('records')).context['page'].object_list), 3)

    def test_staff_cannot_edit_approved_record_and_sees_locked_message(self):
        rec = self.record(by=self.staff, status='approved')
        r = self.as_user(self.staff).get(reverse('record_edit', args=[rec.pk]), follow=True)
        self.assertContains(r, 'This record has already been approved and is locked.')
        data = {'food_item': self.item.pk, 'date': rec.date.isoformat(), 'prepared_quantity': 99,
                'sold_quantity': 1, 'weather': 'x', 'submit_action': 'submit'}
        self.as_user(self.staff).post(reverse('record_edit', args=[rec.pk]), data)
        rec.refresh_from_db()
        self.assertEqual(rec.prepared_quantity, 10)

    def test_staff_cannot_edit_submitted_record_while_waiting(self):
        rec = self.record(by=self.staff, status='submitted')
        r = self.as_user(self.staff).get(reverse('record_edit', args=[rec.pk]), follow=True)
        self.assertContains(r, 'Waiting for manager approval.')

    def test_staff_cannot_manage_items_or_prices(self):
        c = self.as_user(self.staff)
        self.assertEqual(c.post(reverse('food_item_add'), {'name': 'X', 'category': 'Y', 'cost_per_portion': '1'}).status_code, 403)
        self.assertEqual(c.post(reverse('food_item_toggle', args=[self.item.pk])).status_code, 403)
        self.assertEqual(c.post(reverse('food_item_delete', args=[self.item.pk])).status_code, 403)
        self.assertEqual(c.get(reverse('food_item_edit', args=[self.item.pk])).status_code, 403)
        self.item.refresh_from_db()
        self.assertEqual((self.item.is_active, self.item.cost_per_portion), (True, 60))

    def test_staff_cannot_touch_plans_forecasts_or_traffic(self):
        c = self.as_user(self.staff)
        line = Prediction.objects.create(food_item=self.item, date=timezone.localdate(), predicted_demand=10,
                                         recommended_preparation=11, safety_reserve=1)
        self.assertEqual(c.post(reverse('planner_action', args=[line.pk]), {'action': 'approve', 'quantity': '5'}).status_code, 403)
        self.assertEqual(c.post(reverse('planner_approve_all'), {'date': line.date.isoformat()}).status_code, 403)
        self.assertEqual(c.post(reverse('forecast_save'), {}).status_code, 403)
        self.assertEqual(c.post(reverse('traffic_add'), {}).status_code, 403)
        self.assertEqual(c.post(reverse('traffic_fetch')).status_code, 403)
        line.refresh_from_db()
        self.assertEqual(line.status, 'suggested')

    def test_staff_sees_only_approved_plan_lines_without_buttons(self):
        today = timezone.localdate()
        other = FoodItem.objects.create(name='Dosa', category='Main', cost_per_portion=20)
        Prediction.objects.create(food_item=self.item, date=today, predicted_demand=10, recommended_preparation=11, safety_reserve=1)
        Prediction.objects.create(food_item=other, date=today, predicted_demand=20, recommended_preparation=22, safety_reserve=2,
                                  status='approved', approved_quantity=22)
        r = self.as_user(self.staff).get(reverse('planner'), {'date': today.isoformat()})
        self.assertEqual([l.food_item.name for l in r.context['lines']], ['Dosa'])
        self.assertNotContains(r, 'name="action"')

    def test_staff_cannot_see_or_reach_the_activity_log_or_users(self):
        c = self.as_user(self.staff)
        for name in ('activity_log', 'user_list', 'user_add'):
            self.assertEqual(c.get(reverse(name)).status_code, 403)
        self.assertEqual(c.post(reverse('user_toggle_active', args=[self.manager.pk])).status_code, 403)

    def test_all_state_changing_urls_reject_get(self):
        c = self.as_user(self.owner)
        rec = self.record(by=self.staff)
        for url in (reverse('record_delete', args=[rec.pk]), reverse('record_review', args=[rec.pk]),
                    reverse('user_toggle_active', args=[self.staff.pk])):
            self.assertEqual(c.get(url).status_code, 405)

    def test_csrf_is_enforced_on_posts(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True); c.force_login(self.owner)
        rec = self.record(by=self.staff)
        self.assertEqual(c.post(reverse('record_delete', args=[rec.pk])).status_code, 403)
        self.assertTrue(FoodRecord.objects.filter(pk=rec.pk).exists())


class ManagerAndOwnerTests(RoleTestCase):
    def test_manager_can_edit_delete_and_manage_items(self):
        rec = self.record(by=self.staff)
        c = self.as_user(self.manager)
        data = {'food_item': self.item.pk, 'date': rec.date.isoformat(), 'prepared_quantity': 20,
                'sold_quantity': 15, 'weather': 'Rainy'}
        self.assertEqual(c.post(reverse('record_edit', args=[rec.pk]), data).status_code, 302)
        rec.refresh_from_db()
        self.assertEqual((rec.prepared_quantity, rec.updated_by), (20, self.manager))
        self.assertEqual(c.post(reverse('food_item_add'), {'name': 'Idli', 'category': 'Breakfast', 'cost_per_portion': '15'}).status_code, 302)
        self.assertTrue(FoodItem.objects.filter(name='Idli', cost_per_portion=15).exists())
        self.assertEqual(c.post(reverse('record_delete', args=[rec.pk])).status_code, 302)
        self.assertFalse(FoodRecord.objects.filter(pk=rec.pk).exists())

    def test_manager_records_are_approved_immediately(self):
        data = {'food_item': self.item.pk, 'date': timezone.localdate().isoformat(), 'prepared_quantity': 10,
                'sold_quantity': 8, 'weather': 'Sunny'}
        self.as_user(self.manager).post(reverse('add_record'), data)
        rec = FoodRecord.objects.get()
        self.assertEqual((rec.status, rec.created_by, rec.approved_by), ('approved', self.manager, self.manager))
        self.assertIsNotNone(rec.approved_at)

    def test_activity_log_for_manager_only_when_granted(self):
        self.assertEqual(self.as_user(self.manager).get(reverse('activity_log')).status_code, 403)
        data = {'first_name': 'Manu', 'last_name': '', 'email': self.manager.email, 'phone': '', 'role': 'manager',
                'view_activity': 'on'}
        self.assertEqual(self.as_user(self.owner).post(reverse('user_edit', args=[self.manager.pk]), data).status_code, 302)
        self.manager = User.objects.get(pk=self.manager.pk)
        self.assertEqual(self.as_user(self.manager).get(reverse('activity_log')).status_code, 200)
        data.pop('view_activity')
        self.as_user(self.owner).post(reverse('user_edit', args=[self.manager.pk]), data)
        self.manager = User.objects.get(pk=self.manager.pk)
        self.assertEqual(self.as_user(self.manager).get(reverse('activity_log')).status_code, 403)

    def test_manager_cannot_use_user_management_to_escalate(self):
        c = self.as_user(self.manager)
        data = {'first_name': 'E', 'email': 'e@example.com', 'role': 'owner', 'password': 'Sturdy-pass-928'}
        self.assertEqual(c.post(reverse('user_add'), data).status_code, 403)
        self.assertEqual(c.post(reverse('user_edit', args=[self.manager.pk]), {**data, 'email': self.manager.email}).status_code, 403)
        self.assertEqual(UserProfile.for_user(self.manager).role, 'manager')
        self.assertFalse(User.objects.filter(email='e@example.com').exists())


class ApprovalWorkflowTests(RoleTestCase):
    def submit(self, user, action='submit', **over):
        data = {'food_item': self.item.pk, 'date': (timezone.localdate() - timedelta(days=1)).isoformat(),
                'prepared_quantity': 30, 'sold_quantity': 20, 'weather': 'Sunny', 'submit_action': action, **over}
        return self.as_user(user).post(reverse('add_record'), data, follow=True)

    def test_staff_submit_creates_submitted_record_with_message(self):
        r = self.submit(self.staff)
        rec = FoodRecord.objects.get()
        self.assertEqual((rec.status, rec.created_by, rec.approved_by), ('submitted', self.staff, None))
        self.assertIsNotNone(rec.submitted_at)
        self.assertContains(r, 'Your record has been submitted successfully.')
        self.assertContains(r, 'Waiting for manager approval.')

    def test_staff_can_save_a_draft_and_later_edit_and_submit_it(self):
        self.submit(self.staff, action='draft')
        rec = FoodRecord.objects.get()
        self.assertEqual(rec.status, 'draft')
        data = {'food_item': self.item.pk, 'date': rec.date.isoformat(), 'prepared_quantity': 31,
                'sold_quantity': 20, 'weather': 'Sunny', 'submit_action': 'submit'}
        self.as_user(self.staff).post(reverse('record_edit', args=[rec.pk]), data)
        rec.refresh_from_db()
        self.assertEqual((rec.status, rec.prepared_quantity), ('submitted', 31))

    def test_unapproved_records_do_not_count_until_approved(self):
        self.submit(self.staff)
        rec = FoodRecord.objects.get()
        self.assertEqual(FoodRecord.objects.counted().count(), 0)
        ctx = self.as_user(self.owner).get(reverse('dashboard')).context
        self.assertFalse(ctx['has_records'])                  # nothing approved yet -> nothing counted
        self.as_user(self.manager).post(reverse('record_review', args=[rec.pk]), {'action': 'approve'})
        self.assertEqual(FoodRecord.objects.counted().count(), 1)
        ctx = self.as_user(self.owner).get(reverse('dashboard')).context
        self.assertEqual(ctx['totals']['prepared'], 30)

    def test_manager_approves_and_it_locks_for_staff(self):
        self.submit(self.staff)
        rec = FoodRecord.objects.get()
        r = self.as_user(self.manager).post(reverse('record_review', args=[rec.pk]), {'action': 'approve'}, follow=True)
        rec.refresh_from_db()
        self.assertEqual((rec.status, rec.approved_by), ('approved', self.manager))
        self.assertIsNotNone(rec.approved_at)
        self.assertContains(r, 'Approved')
        locked = self.as_user(self.staff).get(reverse('record_edit', args=[rec.pk]), follow=True)
        self.assertContains(locked, 'This record has already been approved and is locked.')

    def test_rejection_needs_a_reason_then_staff_can_fix_and_resubmit(self):
        self.submit(self.staff)
        rec = FoodRecord.objects.get()
        c = self.as_user(self.manager)
        c.post(reverse('record_review', args=[rec.pk]), {'action': 'reject', 'note': ''})
        rec.refresh_from_db(); self.assertEqual(rec.status, 'submitted')
        c.post(reverse('record_review', args=[rec.pk]), {'action': 'reject', 'note': 'Sold count looks wrong'})
        rec.refresh_from_db()
        self.assertEqual((rec.status, rec.review_note), ('rejected', 'Sold count looks wrong'))
        self.assertContains(self.as_user(self.staff).get(reverse('dashboard')), 'Sold count looks wrong')
        data = {'food_item': self.item.pk, 'date': rec.date.isoformat(), 'prepared_quantity': 30,
                'sold_quantity': 25, 'weather': 'Sunny', 'submit_action': 'submit'}
        self.as_user(self.staff).post(reverse('record_edit', args=[rec.pk]), data)
        rec.refresh_from_db()
        self.assertEqual((rec.status, rec.sold_quantity, rec.review_note), ('submitted', 25, ''))

    def test_staff_cannot_approve_their_own_record(self):
        self.submit(self.staff)
        rec = FoodRecord.objects.get()
        self.assertEqual(self.as_user(self.staff).post(reverse('record_review', args=[rec.pk]), {'action': 'approve'}).status_code, 403)
        rec.refresh_from_db(); self.assertEqual(rec.status, 'submitted')

    def test_only_submitted_records_can_be_reviewed(self):
        rec = self.record(by=self.staff, status='approved')
        self.as_user(self.manager).post(reverse('record_review', args=[rec.pk]), {'action': 'reject', 'note': 'x'})
        rec.refresh_from_db(); self.assertEqual(rec.status, 'approved')

    def test_pending_count_notification_for_approvers(self):
        self.submit(self.staff)
        self.assertEqual(self.as_user(self.manager).get(reverse('dashboard')).context['pending_count'], 1)
        self.assertContains(self.as_user(self.manager).get(reverse('notifications')), 'Waiting for your approval')

    def test_staff_cannot_inject_a_status_through_the_form(self):
        self.submit(self.staff, status='approved', approved_by=self.owner.pk)
        rec = FoodRecord.objects.get()
        self.assertEqual((rec.status, rec.approved_by), ('submitted', None))

    def test_duplicate_item_and_date_is_rejected(self):
        self.submit(self.staff)
        self.submit(self.staff)
        self.assertEqual(FoodRecord.objects.count(), 1)

    def test_status_filter_on_records_page(self):
        self.record(by=self.staff, status='approved', days_ago=1)
        self.record(by=self.staff, status='draft', days_ago=2)
        r = self.as_user(self.manager).get(reverse('records'), {'status': 'draft'})
        self.assertEqual([x.status for x in r.context['page'].object_list], ['draft'])


class ExistingDataCompatibilityTests(RoleTestCase):
    def test_a_record_created_the_old_way_is_approved_and_counted(self):
        rec = FoodRecord.objects.create(food_item=self.item, date=timezone.localdate(), prepared_quantity=10,
                                        sold_quantity=9, weather='Sunny')
        self.assertEqual((rec.status, rec.created_by), ('approved', None))
        self.assertEqual(FoodRecord.objects.counted().count(), 1)
        ctx = self.as_user(self.owner).get(reverse('dashboard')).context
        self.assertEqual(ctx['totals']['prepared'], 10)

    def test_legacy_record_without_an_author_is_editable_by_manager_only(self):
        rec = FoodRecord.objects.create(food_item=self.item, date=timezone.localdate(), prepared_quantity=10,
                                        sold_quantity=9, weather='Sunny')
        self.assertEqual(self.as_user(self.manager).get(reverse('record_edit', args=[rec.pk])).status_code, 200)
        self.assertEqual(self.as_user(self.staff).get(reverse('record_edit', args=[rec.pk])).status_code, 403)

    def test_role_data_migration_maps_old_roles(self):
        import importlib
        from django.apps import apps as real_apps
        mig = importlib.import_module('predictor.migrations.0006_migrate_roles_and_existing_records')
        UserProfile.objects.filter(user=self.staff).update(role='kitchen')
        UserProfile.objects.filter(user=self.other_staff).update(role='other')
        root = User.objects.create_superuser('root', 'root@example.com', PW)
        UserProfile.objects.filter(user=root).delete()
        mig.forwards(real_apps, None)
        self.assertEqual(UserProfile.objects.get(user=self.staff).role, 'staff')
        self.assertEqual(UserProfile.objects.get(user=self.other_staff).role, 'staff')
        self.assertEqual(UserProfile.objects.get(user=root).role, 'owner')
        self.assertEqual(UserProfile.objects.get(user=self.manager).role, 'manager')   # kept


class UserManagementTests(RoleTestCase):
    def test_owner_creates_a_manager_with_a_hashed_password(self):
        data = {'first_name': 'Mira', 'last_name': 'K', 'email': 'mira@example.com', 'phone': '', 'role': 'manager',
                'password': 'Sturdy-pass-928'}
        r = self.as_user(self.owner).post(reverse('user_add'), data)
        self.assertRedirects(r, reverse('user_list'), fetch_redirect_response=False)
        u = User.objects.get(email='mira@example.com')
        self.assertEqual(u.profile.role, 'manager')
        self.assertNotEqual(u.password, 'Sturdy-pass-928')
        self.assertTrue(u.password.startswith(('pbkdf2_', 'argon2', 'bcrypt', 'scrypt')))
        self.assertTrue(u.check_password('Sturdy-pass-928'))
        self.assertFalse(u.is_staff or u.is_superuser)

    def test_new_user_without_password_gets_a_reset_email_not_a_known_password(self):
        from django.core import mail
        self.as_user(self.owner).post(reverse('user_add'), {'first_name': 'Nia', 'email': 'nia@example.com', 'role': 'staff'})
        u = User.objects.get(email='nia@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/password-reset/', mail.outbox[0].body)
        self.assertTrue(u.has_usable_password())          # random, never shown

    def test_owner_can_send_a_reset_link_for_a_user(self):
        from django.core import mail
        self.as_user(self.owner).post(reverse('user_send_reset', args=[self.staff.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.staff.email])

    def test_weak_temporary_password_is_rejected(self):
        r = self.as_user(self.owner).post(reverse('user_add'), {'first_name': 'W', 'email': 'w@example.com', 'role': 'staff', 'password': '12345678'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email='w@example.com').exists())

    def test_deactivate_and_reactivate_keeps_records(self):
        self.record(by=self.staff)
        c = self.as_user(self.owner)
        c.post(reverse('user_toggle_active', args=[self.staff.pk]))
        self.staff.refresh_from_db(); self.assertFalse(self.staff.is_active)
        self.assertEqual(FoodRecord.objects.filter(created_by=self.staff).count(), 1)
        self.client.logout()
        r = self.client.post(reverse('login'), {'identifier': 'sam', 'password': PW})
        self.assertContains(r, 'deactivated')
        c = self.as_user(self.owner)
        c.post(reverse('user_toggle_active', args=[self.staff.pk]))
        self.staff.refresh_from_db(); self.assertTrue(self.staff.is_active)

    def test_deactivated_user_is_locked_out_of_an_existing_session(self):
        c = self.as_user(self.staff)
        self.assertEqual(c.get(reverse('dashboard')).status_code, 200)
        User.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(c.get(reverse('dashboard')).status_code, 302)

    def test_owner_cannot_deactivate_or_demote_themselves(self):
        c = self.as_user(self.owner)
        c.post(reverse('user_toggle_active', args=[self.owner.pk]))
        self.owner.refresh_from_db(); self.assertTrue(self.owner.is_active)
        data = {'first_name': 'Olive', 'email': self.owner.email, 'role': 'staff'}
        c.post(reverse('user_edit', args=[self.owner.pk]), data)
        self.assertEqual(UserProfile.for_user(self.owner).role, 'owner')

    def test_last_owner_and_superuser_are_protected(self):
        root = User.objects.create_superuser('root', 'root@example.com', PW)
        c = self.as_user(self.owner)
        c.post(reverse('user_toggle_active', args=[root.pk]))
        root.refresh_from_db(); self.assertTrue(root.is_active)
        c.post(reverse('user_edit', args=[root.pk]), {'first_name': 'R', 'email': root.email, 'role': 'staff'})
        from .access import role_of
        self.assertEqual(role_of(User.objects.get(pk=root.pk)), 'owner')

    def test_a_second_owner_can_be_deactivated_but_not_the_last(self):
        second = make_user('omar', 'owner')
        c = self.as_user(self.owner)
        c.post(reverse('user_toggle_active', args=[second.pk]))
        second.refresh_from_db(); self.assertFalse(second.is_active)
        # `olive` is now the only active owner; another owner (reactivated) tries to remove her
        second.is_active = True; second.save()
        c2 = self.as_user(second)
        User.objects.filter(pk=self.owner.pk).update(is_active=True)
        c2.post(reverse('user_toggle_active', args=[self.owner.pk]))   # two owners -> allowed
        self.owner.refresh_from_db(); self.assertFalse(self.owner.is_active)
        c2.post(reverse('user_toggle_active', args=[second.pk]))       # self -> refused
        second.refresh_from_db(); self.assertTrue(second.is_active)

    def test_owner_can_delete_deactivated_team_member(self):
        self.staff.is_active = False
        self.staff.save()
        staff_pk = self.staff.pk

        r = self.as_user(self.owner).get(reverse('user_list'))
        self.assertContains(r, reverse('user_delete', args=[staff_pk]))

        res = self.as_user(self.owner).post(reverse('user_delete', args=[staff_pk]))
        self.assertRedirects(res, reverse('user_list'), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=staff_pk).exists())

    def test_active_account_cannot_be_directly_deleted(self):
        staff_pk = self.staff.pk
        self.assertTrue(self.staff.is_active)

        r = self.as_user(self.owner).get(reverse('user_list'))
        self.assertNotContains(r, reverse('user_delete', args=[staff_pk]))

        res = self.as_user(self.owner).post(reverse('user_delete', args=[staff_pk]))
        self.assertRedirects(res, reverse('user_list'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=staff_pk).exists())
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)

    def test_staff_and_manager_cannot_delete_accounts(self):
        self.staff.is_active = False
        self.staff.save()
        staff_pk = self.staff.pk

        r_staff = self.as_user(self.other_staff).post(reverse('user_delete', args=[staff_pk]))
        self.assertEqual(r_staff.status_code, 403)

        r_mgr = self.as_user(self.manager).post(reverse('user_delete', args=[staff_pk]))
        self.assertEqual(r_mgr.status_code, 403)

        self.assertTrue(User.objects.filter(pk=staff_pk).exists())

    def test_owner_cannot_delete_themselves(self):
        owner_pk = self.owner.pk
        res = self.as_user(self.owner).post(reverse('user_delete', args=[owner_pk]))
        self.assertRedirects(res, reverse('user_list'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=owner_pk).exists())

    def test_last_owner_and_superuser_are_protected_from_deletion(self):
        root = User.objects.create_superuser('root', 'root@example.com', PW)
        root.is_active = False
        root.save()
        r = self.as_user(self.owner).post(reverse('user_delete', args=[root.pk]))
        self.assertRedirects(r, reverse('user_list'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=root.pk).exists())

        sys_admin = User.objects.create_user('admin', 'admin@example.com', PW)
        sys_admin.is_active = False
        sys_admin.save()
        r = self.as_user(self.owner).post(reverse('user_delete', args=[sys_admin.pk]))
        self.assertTrue(User.objects.filter(pk=sys_admin.pk).exists())

        second_owner = make_user('omar', 'owner')
        second_owner.is_active = False
        second_owner.save()
        r = self.as_user(self.owner).post(reverse('user_delete', args=[second_owner.pk]))
        self.assertFalse(User.objects.filter(pk=second_owner.pk).exists())

    def test_historical_records_remain_accessible_after_account_deletion(self):
        rec = self.record(by=self.staff)
        staff_pk = self.staff.pk
        staff_username = self.staff.username

        from .models import PreparationBatch
        batch = PreparationBatch.objects.create(
            food_item=self.item, date=timezone.localdate(), planned_quantity=20,
            assigned_to=self.staff, created_by=self.staff, submitted_by=self.staff
        )

        self.staff.is_active = False
        self.staff.save()
        r = self.as_user(self.owner).post(reverse('user_delete', args=[staff_pk]))
        self.assertRedirects(r, reverse('user_list'), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=staff_pk).exists())

        rec.refresh_from_db()
        self.assertIsNone(rec.created_by)
        self.assertEqual(rec.prepared_quantity, 10)
        self.assertEqual(rec.sold_quantity, 7)

        batch.refresh_from_db()
        self.assertIsNone(batch.assigned_to)
        self.assertEqual(batch.planned_quantity, 20)

        c = self.as_user(self.owner)
        self.assertEqual(c.get(reverse('records')).status_code, 200)
        self.assertEqual(c.get(reverse('kitchen')).status_code, 200)
        self.assertEqual(c.get(reverse('batch_detail', args=[batch.pk])).status_code, 200)

        self.assertTrue(ActivityLog.objects.filter(object_repr=staff_username).exists())

    def test_owner_assigns_roles_and_the_menu_follows(self):
        data = {'first_name': 'Sam', 'email': self.staff.email, 'role': 'manager'}
        self.as_user(self.owner).post(reverse('user_edit', args=[self.staff.pk]), data)
        self.staff = User.objects.get(pk=self.staff.pk)
        self.assertEqual(self.as_user(self.staff).get(reverse('approvals')).status_code, 200)

    def test_manage_items_grant_lets_staff_edit_items_but_not_prices(self):
        data = {'first_name': 'Sam', 'email': self.staff.email, 'role': 'staff', 'manage_items': 'on'}
        self.as_user(self.owner).post(reverse('user_edit', args=[self.staff.pk]), data)
        self.staff = User.objects.get(pk=self.staff.pk)
        c = self.as_user(self.staff)
        self.assertEqual(c.get(reverse('food_item_edit', args=[self.item.pk])).status_code, 200)
        self.assertNotContains(c.get(reverse('food_item_edit', args=[self.item.pk])), 'cost_per_portion')
        c.post(reverse('food_item_edit', args=[self.item.pk]),
               {'name': 'Thali Deluxe', 'category': 'Main', 'cost_per_portion': '1'})
        self.item.refresh_from_db()
        self.assertEqual((self.item.name, self.item.cost_per_portion), ('Thali Deluxe', 60))   # price untouched
        self.assertEqual(c.post(reverse('food_item_delete', args=[self.item.pk])).status_code, 403)

    def test_pending_signup_is_approved_by_the_owner_and_can_then_sign_in(self):
        self.client.logout()
        self.client.post(reverse('signup'), {'full_name': 'New Hire', 'email': 'hire@example.com',
                                             'password1': 'Sturdy-pass-928', 'password2': 'Sturdy-pass-928', 'accept_terms': 'on'})
        hire = User.objects.get(email='hire@example.com')
        self.assertContains(self.as_user(self.owner).get(reverse('user_list')), 'Pending approval')
        self.as_user(self.owner).post(reverse('user_approve', args=[hire.pk]), {'decision': 'approve'})
        self.client.logout()
        r = self.client.post(reverse('login'), {'identifier': 'hire@example.com', 'password': 'Sturdy-pass-928'})
        self.assertRedirects(r, reverse('dashboard'), fetch_redirect_response=False)

    def test_declining_a_signup_removes_it(self):
        self.client.logout()
        self.client.post(reverse('signup'), {'full_name': 'No Way', 'email': 'no@example.com',
                                             'password1': 'Sturdy-pass-928', 'password2': 'Sturdy-pass-928', 'accept_terms': 'on'})
        u = User.objects.get(email='no@example.com')
        self.as_user(self.owner).post(reverse('user_approve', args=[u.pk]), {'decision': 'decline'})
        self.assertFalse(User.objects.filter(pk=u.pk).exists())

    def test_approve_endpoint_cannot_activate_arbitrary_deactivated_users(self):
        User.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(self.as_user(self.owner).post(reverse('user_approve', args=[self.staff.pk]), {'decision': 'approve'}).status_code, 404)

    def test_user_list_search_and_filters(self):
        r = self.as_user(self.owner).get(reverse('user_list'), {'role': 'manager'})
        self.assertEqual([u.username for u in r.context['page'].object_list], ['manu'])
        r = self.as_user(self.owner).get(reverse('user_list'), {'q': 'sana'})
        self.assertEqual([u.username for u in r.context['page'].object_list], ['sana'])


class ActivityLogTests(RoleTestCase):
    def test_record_lifecycle_is_logged_with_old_and_new_values(self):
        c = self.as_user(self.staff)
        data = {'food_item': self.item.pk, 'date': (timezone.localdate() - timedelta(days=1)).isoformat(),
                'prepared_quantity': 30, 'sold_quantity': 20, 'weather': 'Sunny', 'submit_action': 'submit'}
        c.post(reverse('add_record'), data)
        rec = FoodRecord.objects.get()
        self.as_user(self.manager).post(reverse('record_review', args=[rec.pk]), {'action': 'approve'})
        self.as_user(self.manager).post(reverse('record_edit', args=[rec.pk]), {**data, 'prepared_quantity': 40})
        self.as_user(self.manager).post(reverse('record_delete', args=[rec.pk]))
        actions = list(ActivityLog.objects.order_by('id').values_list('action', flat=True))
        self.assertEqual(actions, ['record_submit', 'record_approve', 'record_update', 'record_delete'])
        update = ActivityLog.objects.get(action='record_update')
        self.assertEqual((update.old_values['prepared_quantity'], update.new_values['prepared_quantity']), (30, 40))
        self.assertEqual(update.user, self.manager)
        approve = ActivityLog.objects.get(action='record_approve')
        self.assertEqual((approve.old_values['status'], approve.new_values['status']), ('submitted', 'approved'))

    def test_item_changes_predictions_and_plans_are_logged(self):
        c = self.as_user(self.manager)
        c.post(reverse('food_item_add'), {'name': 'Idli', 'category': 'B', 'cost_per_portion': '10'})
        idli = FoodItem.objects.get(name='Idli')
        c.post(reverse('food_item_edit', args=[idli.pk]), {'name': 'Idli', 'category': 'B', 'cost_per_portion': '12'})
        c.post(reverse('food_item_toggle', args=[idli.pk]))
        edit = ActivityLog.objects.get(action='item_update')
        self.assertEqual((edit.old_values['cost_per_portion'], edit.new_values['cost_per_portion']), ('10.00', '12.00'))
        self.assertTrue(ActivityLog.objects.filter(action='item_create').exists())
        self.assertTrue(ActivityLog.objects.filter(action='item_toggle').exists())
        line = Prediction.objects.create(food_item=self.item, date=timezone.localdate(), predicted_demand=10,
                                         recommended_preparation=11, safety_reserve=1)
        c.post(reverse('planner_action', args=[line.pk]), {'action': 'approve', 'quantity': '11'})
        self.assertTrue(ActivityLog.objects.filter(action='plan_update').exists())

    def test_user_creation_role_change_and_deactivation_are_logged(self):
        c = self.as_user(self.owner)
        c.post(reverse('user_add'), {'first_name': 'Nia', 'email': 'nia@example.com', 'role': 'staff', 'password': 'Sturdy-pass-928'})
        nia = User.objects.get(email='nia@example.com')
        c.post(reverse('user_edit', args=[nia.pk]), {'first_name': 'Nia', 'email': nia.email, 'role': 'manager'})
        c.post(reverse('user_toggle_active', args=[nia.pk]))
        got = set(ActivityLog.objects.values_list('action', flat=True))
        self.assertTrue({'user_create', 'user_role', 'user_deactivate'} <= got)
        role = ActivityLog.objects.get(action='user_role')
        self.assertEqual((role.old_values['role'], role.new_values['role']), ('staff', 'manager'))

    def test_no_secrets_ever_reach_the_log(self):
        c = self.as_user(self.owner)
        c.post(reverse('user_add'), {'first_name': 'Nia', 'email': 'nia@example.com', 'role': 'staff', 'password': 'Sturdy-pass-928'})
        dump = ' '.join(f'{e.summary} {e.object_repr} {e.old_values} {e.new_values}' for e in ActivityLog.objects.all())
        self.assertNotIn('Sturdy-pass-928', dump)
        from .access import clean_values
        self.assertEqual(clean_values({'password': 'x', 'api_token': 'y', 'SECRET_KEY': 'z', 'ok': 1}), {'ok': 1})

    def test_forecast_generation_is_logged(self):
        for d in range(1, 30):
            FoodRecord.objects.create(food_item=self.item, date=timezone.localdate() - timedelta(days=d),
                                      prepared_quantity=20, sold_quantity=15, weather='Sunny')
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.as_user(self.manager).post(reverse('forecast_save'), {'date': tomorrow.isoformat(), 'item': 'all'})
        self.assertTrue(ActivityLog.objects.filter(action='prediction').exists())

    def test_log_page_shows_dd_mm_yyyy_and_filters(self):
        from .access import log_activity
        log_activity(self.owner, 'settings', summary='changed')
        r = self.as_user(self.owner).get(reverse('activity_log'))
        self.assertContains(r, timezone.localtime().strftime('%d-%m-%Y'))
        r = self.as_user(self.owner).get(reverse('activity_log'), {'action': 'record_approve'})
        self.assertEqual(len(r.context['page'].object_list), 0)


class ManagementCommandTests(TestCase):
    def test_create_owner_promotes_existing_user(self):
        from django.core.management import call_command
        u = User.objects.create_user('zed', 'zed@example.com', PW)
        call_command('create_owner', promote='zed', stdout=__import__('io').StringIO())
        self.assertEqual(UserProfile.for_user(u).role, 'owner')
        self.assertTrue(u.groups.filter(name='Owner').exists())

    def test_create_dev_users_makes_three_hashed_accounts_and_refuses_without_debug(self):
        import io
        from django.core.management import call_command
        from django.core.management.base import CommandError
        out = io.StringIO()
        with self.settings(DEBUG=True):
            call_command('create_dev_users', stdout=out)
        roles = {u.username: u.profile.role for u in User.objects.filter(username__startswith='dev-')}
        self.assertEqual(roles, {'dev-owner': 'owner', 'dev-manager': 'manager', 'dev-staff': 'staff'})
        for u in User.objects.filter(username__startswith='dev-'):
            self.assertFalse(u.is_staff or u.is_superuser)
            self.assertIn(f'username: {u.username}', out.getvalue())
        with self.settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command('create_dev_users', stdout=io.StringIO())


class EmailAndAccountTests(RoleTestCase):
    def test_integrations_tab_is_owner_only(self):
        self.assertContains(self.as_user(self.owner).get(reverse('settings')), 'data-tab="integrations"')
        for user in (self.manager, self.staff):
            self.assertNotContains(self.as_user(user).get(reverse('settings')), 'data-tab="integrations"')

    def test_forgot_password_sends_a_working_link_to_active_users_only(self):
        from django.core import mail
        self.client.logout()
        r = self.client.post(reverse('password_reset'), {'email': self.staff.email})
        self.assertRedirects(r, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        link = [w for w in mail.outbox[0].body.split() if '/password-reset/' in w][0]
        path = link.split('testserver', 1)[1]
        page = self.client.get(path, follow=True)
        self.assertContains(page, 'password')
        User.objects.filter(pk=self.other_staff.pk).update(is_active=False)
        self.client.post(reverse('password_reset'), {'email': self.other_staff.email})
        self.assertEqual(len(mail.outbox), 1)                     # nothing sent to the deactivated account
        self.assertContains(self.client.get(reverse('login')), 'Forgot password?')

    def test_settings_refuse_to_start_in_production_with_the_dev_key(self):
        import subprocess, sys, os
        env = {**os.environ, 'DJANGO_DEBUG': '0', 'DJANGO_SETTINGS_MODULE': 'foodwaste_project.settings'}
        env.pop('DJANGO_SECRET_KEY', None)
        out = subprocess.run([sys.executable, '-c', 'import django;django.setup()'], env=env, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', out.stderr)


class DeploymentConfigTests(TestCase):
    """Feature 7: DATABASE_URL, environment-driven settings and static files."""

    def test_no_database_url_uses_local_sqlite(self):
        from pathlib import Path
        from foodwaste_project.dbconfig import database_from_url
        cfg = database_from_url('', Path('/proj'))
        self.assertEqual(cfg['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(Path(cfg['NAME']), Path('/proj/db.sqlite3'))

    def test_postgres_url_is_parsed_without_leaking_into_source(self):
        from pathlib import Path
        from foodwaste_project.dbconfig import database_from_url
        cfg = database_from_url('postgres://fw_user:p%40ss%2Fword@db.internal:5433/foodwaste?sslmode=require', Path('/proj'))
        self.assertEqual(cfg['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual((cfg['NAME'], cfg['USER'], cfg['HOST'], cfg['PORT']), ('foodwaste', 'fw_user', 'db.internal', '5433'))
        self.assertEqual(cfg['PASSWORD'], 'p@ss/word')                  # URL-decoded
        self.assertEqual(cfg['OPTIONS'], {'sslmode': 'require'})
        self.assertGreater(cfg['CONN_MAX_AGE'], 0)

    def test_bad_urls_are_rejected(self):
        from pathlib import Path
        from foodwaste_project.dbconfig import database_from_url
        for bad in ('mysql://u:p@h/db', 'postgres://u:p@h', 'postgres:///db'):
            with self.assertRaises(ValueError):
                database_from_url(bad, Path('/proj'))

    def test_production_settings_load_with_postgres_and_render_host(self):
        import os, subprocess, sys
        env = {**os.environ, 'DJANGO_DEBUG': '0', 'DJANGO_SECRET_KEY': 'x' * 60, 'RENDER_EXTERNAL_HOSTNAME': 'foodwaste.onrender.com',
               'DATABASE_URL': 'postgres://u:p@h:5432/d', 'DJANGO_SETTINGS_MODULE': 'foodwaste_project.settings'}
        code = ("import django;django.setup();from django.conf import settings as s;"
                "print(s.DATABASES['default']['ENGINE'], s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS, s.DEBUG, s.SESSION_COOKIE_SECURE)")
        out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('postgresql', out.stdout)
        self.assertIn("'foodwaste.onrender.com'", out.stdout)
        self.assertIn("'https://foodwaste.onrender.com'", out.stdout)
        self.assertTrue(out.stdout.strip().endswith('False True'))

    def test_deployment_files_exist_and_contain_no_secrets(self):
        from pathlib import Path
        from django.conf import settings as s
        root = Path(s.BASE_DIR)
        for name in ('requirements.txt', 'build.sh', 'render.yaml', '.env.example', '.gitignore'):
            self.assertTrue((root / name).exists(), name)
        example = (root / '.env.example').read_text()
        self.assertNotRegex(example, r'(?i)(sk_|AIza|password=[^\s]{6,})')
        self.assertIn('.env', (root / '.gitignore').read_text().split())
