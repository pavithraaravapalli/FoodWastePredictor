"""Weather, traffic and event intelligence: automatic mode (mocked providers) and manual fallback."""
import io
import json
import urllib.error
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ActivityLog, FoodRecord, LocalEvent, RestaurantSettings, TrafficObservation, WeatherObservation
from .services import events as events_svc
from .services import traffic as traffic_svc
from .services import weather as weather_svc
from .services.http import ProviderError
from .tests import RoleTestCase

URLOPEN = 'urllib.request.urlopen'


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def weather_payload(day=None):
    day = day or timezone.localdate()
    days = [(day + timedelta(days=i)).isoformat() for i in range(3)]
    return {
        'current': {'temperature_2m': 31.4, 'relative_humidity_2m': 62, 'precipitation': 0.0, 'weather_code': 2, 'wind_speed_10m': 11.2},
        'daily': {'time': days, 'weather_code': [2, 61, 95], 'temperature_2m_max': [33, 29, 27], 'temperature_2m_min': [24, 23, 22],
                  'precipitation_sum': [0, 8.5, 30], 'precipitation_probability_max': [10, 80, 95]},
    }


def event_payload(*events):
    return {'_embedded': {'events': list(events)}}


def tm_event(eid='E1', name='Diwali Mela', venue='Town Ground', when='2099-01-01T14:30:00Z', segment='Miscellaneous', distance=3.26):
    return {'id': eid, 'name': name, 'url': 'https://example.test/e', 'distance': distance,
            'dates': {'start': {'dateTime': when}},
            'classifications': [{'segment': {'name': segment}, 'genre': {'name': 'Fairs & Festivals'}}],
            '_embedded': {'venues': [{'name': venue, 'city': {'name': 'Vapi'}, 'address': {'line1': 'Main Rd'}}]}}


class IntelTestCase(RoleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.restaurant = RestaurantSettings.load()
        self.restaurant.latitude, self.restaurant.longitude = '20.3717', '72.9050'
        self.restaurant.save()


class WeatherTests(IntelTestCase):
    def test_automatic_retrieval_stores_current_and_forecast_with_source_and_timestamps(self):
        with mock.patch(URLOPEN, return_value=response(weather_payload())):
            added, msg = weather_svc.refresh(self.restaurant)
        self.assertEqual(added, 4)                                   # 1 current + 3 forecast days
        cur = WeatherObservation.objects.get(kind='current')
        self.assertEqual((cur.source, cur.provider, cur.condition), ('auto', 'Open-Meteo', 'cloudy'))
        self.assertEqual(str(cur.temperature_c), '31.4')
        self.assertIsNotNone(cur.fetched_at)
        rain = WeatherObservation.objects.get(kind='forecast', condition='rainy')
        self.assertEqual((rain.precipitation_probability, str(rain.precipitation_mm)), (80, '8.5'))
        self.assertTrue(WeatherObservation.objects.filter(condition='stormy').exists())

    def test_condition_codes(self):
        self.assertEqual([weather_svc.condition_for_code(c) for c in (0, 2, 61, 81, 95, 71, None)],
                         ['sunny', 'cloudy', 'rainy', 'rainy', 'stormy', 'other', 'other'])

    def test_cache_prevents_repeat_calls_and_history_is_kept(self):
        with mock.patch(URLOPEN, return_value=response(weather_payload())) as m:
            weather_svc.refresh(self.restaurant)
            added, msg = weather_svc.refresh(self.restaurant)        # inside the cache window
        self.assertEqual((added, m.call_count), (0, 1))
        later = timezone.now() + timedelta(hours=1)
        with mock.patch(URLOPEN, return_value=response(weather_payload())):
            weather_svc.refresh(self.restaurant, now=later)
        self.assertEqual(WeatherObservation.objects.filter(kind='current').count(), 2)   # appended, never overwritten

    def test_api_failures_become_readable_errors_and_do_not_break_pages(self):
        cases = [(urllib.error.URLError('down'), 'Could not reach'),
                 (urllib.error.HTTPError('u', 429, 'Too Many', {}, None), 'rate limiting'),
                 (urllib.error.HTTPError('u', 403, 'No', {}, None), 'rejected')]
        for exc, text in cases:
            with mock.patch(URLOPEN, side_effect=exc):
                with self.assertRaisesRegex(ProviderError, text):
                    weather_svc.refresh(self.restaurant)
        self.restaurant.weather_mode = 'auto'; self.restaurant.save()
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError('down')):
            page = self.as_user(self.manager).get(reverse('weather'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Could not reach')
        self.assertContains(page, 'unavailable')

    def test_missing_location_is_explained(self):
        self.restaurant.latitude = self.restaurant.longitude = None; self.restaurant.save()
        with self.assertRaisesRegex(ProviderError, 'location'):
            weather_svc.refresh(RestaurantSettings.load())

    def test_manual_entry_is_stored_with_source_user_and_never_overwrites(self):
        c = self.as_user(self.manager)
        today = timezone.localdate().isoformat()
        for cond, temp in (('sunny', '33'), ('rainy', '24.5')):
            r = c.post(reverse('weather_add'), {'target_date': today, 'condition': cond, 'temperature_c': temp, 'notes': 'x'})
            self.assertRedirects(r, reverse('weather'))
        rows = WeatherObservation.objects.filter(source='manual').order_by('id')
        self.assertEqual([r.condition for r in rows], ['sunny', 'rainy'])
        self.assertEqual(rows[0].created_by, self.manager)
        self.assertEqual(weather_svc.for_date(timezone.localdate()).condition, 'rainy')    # latest manual wins
        self.assertTrue(ActivityLog.objects.filter(action='weather').exists())

    def test_manual_validation(self):
        c = self.as_user(self.manager)
        far = (timezone.localdate() + timedelta(days=60)).isoformat()
        c.post(reverse('weather_add'), {'target_date': far, 'condition': 'sunny'})
        c.post(reverse('weather_add'), {'target_date': timezone.localdate().isoformat(), 'condition': 'sunny', 'temperature_c': '400'})
        self.assertEqual(WeatherObservation.objects.count(), 0)

    def test_manual_entry_beats_automatic_and_mode_is_respected(self):
        day = timezone.localdate() + timedelta(days=1)
        with mock.patch(URLOPEN, return_value=response(weather_payload())):
            weather_svc.refresh(self.restaurant)
        self.assertIsNone(weather_svc.for_date(day, self.restaurant))          # manual mode: automatic rows are not used
        self.restaurant.weather_mode = 'auto'; self.restaurant.save()
        self.assertEqual(weather_svc.for_date(day, self.restaurant).condition, 'rainy')
        WeatherObservation.objects.create(kind='forecast', source='manual', target_date=day, observed_at=timezone.now(), condition='sunny')
        self.assertEqual(weather_svc.for_date(day, self.restaurant).condition, 'sunny')

    def test_stale_status(self):
        old = WeatherObservation.objects.create(kind='current', source='auto', provider='Open-Meteo', target_date=timezone.localdate(),
                                                observed_at=timezone.now() - timedelta(hours=5), fetched_at=timezone.now() - timedelta(hours=5), condition='sunny')
        self.assertEqual(weather_svc.status(old)[0], 'stale')
        self.assertEqual(weather_svc.status(None)[0], 'none')

    def test_forecast_still_works_without_weather_and_prefills_when_available(self):
        for d in range(1, 8):
            FoodRecord.objects.create(food_item=self.item, date=timezone.localdate() - timedelta(days=d), prepared_quantity=20, sold_quantity=15, weather='Sunny')
        c = self.as_user(self.manager)
        page = c.get(reverse('forecast'), {'generate': 1, 'date': (timezone.localdate() + timedelta(days=1)).isoformat()})
        self.assertEqual(page.status_code, 200)                                 # no weather at all
        WeatherObservation.objects.create(kind='forecast', source='manual', target_date=timezone.localdate() + timedelta(days=1),
                                          observed_at=timezone.now(), condition='rainy')
        page = c.get(reverse('forecast'), {'date': (timezone.localdate() + timedelta(days=1)).isoformat()})
        self.assertContains(page, 'value="Rainy"')
        self.assertContains(page, 'Filled from manual entry')

    def test_weather_permissions(self):
        for user, code in ((self.owner, 200), (self.manager, 200), (self.staff, 403)):
            self.assertEqual(self.as_user(user).get(reverse('weather')).status_code, code)
        self.assertEqual(self.as_user(self.staff).post(reverse('weather_add'), {'target_date': timezone.localdate().isoformat(), 'condition': 'sunny'}).status_code, 403)
        self.assertEqual(self.as_user(self.staff).post(reverse('data_mode_set', args=['weather']), {'mode': 'auto'}).status_code, 403)
        self.assertEqual(WeatherObservation.objects.count(), 0)

    def test_staff_can_enter_weather_only_when_granted(self):
        from django.contrib.auth.models import Permission
        self.staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_weatherobservation', 'add_weatherobservation']))
        c = self.as_user(self.staff)
        self.assertEqual(c.get(reverse('weather')).status_code, 200)
        c.post(reverse('weather_add'), {'target_date': timezone.localdate().isoformat(), 'condition': 'cloudy'})
        self.assertEqual(WeatherObservation.objects.filter(source='manual').count(), 1)
        self.assertEqual(c.post(reverse('data_mode_set', args=['weather']), {'mode': 'auto'}).status_code, 403)   # still cannot switch


class DataModeTests(IntelTestCase):
    def test_manager_and_owner_can_switch_modes_and_it_is_logged(self):
        for user in (self.manager, self.owner):
            for which, field in (('weather', 'weather_mode'), ('traffic', 'traffic_mode'), ('events', 'events_mode')):
                self.as_user(user).post(reverse('data_mode_set', args=[which]), {'mode': 'auto'})
                self.assertEqual(getattr(RestaurantSettings.load(), field), 'auto')
                self.as_user(user).post(reverse('data_mode_set', args=[which]), {'mode': 'manual'})
                self.assertEqual(getattr(RestaurantSettings.load(), field), 'manual')
        self.assertTrue(ActivityLog.objects.filter(action='settings', summary__contains='set to auto').exists())

    def test_invalid_input_changes_nothing_and_get_is_refused(self):
        c = self.as_user(self.manager)
        c.post(reverse('data_mode_set', args=['weather']), {'mode': 'bogus'})
        c.post(reverse('data_mode_set', args=['nonsense']), {'mode': 'auto'})
        self.assertEqual(RestaurantSettings.load().weather_mode, 'manual')
        self.assertEqual(c.get(reverse('data_mode_set', args=['weather'])).status_code, 405)

    def test_mode_badge_is_shown(self):
        c = self.as_user(self.manager)
        self.assertContains(c.get(reverse('weather')), 'Manual mode')
        c.post(reverse('data_mode_set', args=['weather']), {'mode': 'auto'})
        with mock.patch(URLOPEN, return_value=response(weather_payload())):
            self.assertContains(c.get(reverse('weather')), 'Automatic mode')


class TrafficSourceTests(IntelTestCase):
    SEG = {'flowSegmentData': {'currentSpeed': 30, 'freeFlowSpeed': 60, 'currentTravelTime': 120, 'freeFlowTravelTime': 60, 'frc': 'FRC1', 'confidence': 0.9}}

    @override_settings(TRAFFIC_ENABLED=True, TRAFFIC_API_KEY='k')
    def test_automatic_reading_is_stored_with_source_delay_and_road_class(self):
        with mock.patch(URLOPEN, return_value=response(self.SEG)):
            obs, msg = traffic_svc.refresh(self.restaurant, user=self.manager, force=True)
        self.assertTrue(obs.is_automatic)
        self.assertEqual((obs.condition, obs.congestion_pct, obs.delay_seconds, obs.road_class), ('moderate', 50, 60, 'Major road'))
        self.assertIsNotNone(obs.fetched_at)

    @override_settings(TRAFFIC_ENABLED=True, TRAFFIC_API_KEY='k')
    def test_api_failure_does_not_break_the_page_or_the_forecast(self):
        self.restaurant.traffic_mode = 'auto'; self.restaurant.save()
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError('down')):
            page = self.as_user(self.manager).get(reverse('traffic'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Could not reach')
        self.assertEqual(TrafficObservation.objects.count(), 0)                   # nothing invented
        self.assertContains(page, 'unavailable')
        self.assertEqual(self.as_user(self.manager).get(reverse('forecast'), {'generate': 1}).status_code, 200)

    @override_settings(TRAFFIC_ENABLED=True, TRAFFIC_API_KEY='k')
    def test_cache_avoids_repeat_calls(self):
        with mock.patch(URLOPEN, return_value=response(self.SEG)) as m:
            traffic_svc.refresh(self.restaurant, force=True)
            obs, msg = traffic_svc.refresh(self.restaurant)
        self.assertEqual((obs, m.call_count), (None, 1))

    def test_manual_entry_with_four_levels_and_labels(self):
        self.assertEqual([l for _, l in TrafficObservation.CONDITIONS], ['Low traffic', 'Moderate traffic', 'Heavy traffic', 'Very heavy traffic'])
        c = self.as_user(self.manager)
        c.post(reverse('traffic_add'), {'condition': 'standstill', 'observed_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'), 'notes': 'accident'})
        obs = TrafficObservation.objects.get()
        self.assertEqual((obs.condition, obs.is_automatic, obs.created_by), ('standstill', False, self.manager))
        self.assertContains(c.get(reverse('traffic')), 'Manual entry')

    @override_settings(TRAFFIC_ENABLED=False)
    def test_without_a_provider_the_page_says_so_and_never_fakes_data(self):
        page = self.as_user(self.manager).get(reverse('traffic'))
        self.assertContains(page, 'not connected')
        self.assertEqual(TrafficObservation.objects.count(), 0)

    def test_staff_cannot_use_traffic_without_a_grant(self):
        self.assertEqual(self.as_user(self.staff).get(reverse('traffic')).status_code, 403)


@override_settings(EVENTS_ENABLED=True, TICKETMASTER_API_KEY='k')
class EventTests(IntelTestCase):
    def test_automatic_discovery_stores_event_fields(self):
        with mock.patch(URLOPEN, return_value=response(event_payload(tm_event()))):
            created, updated, skipped = events_svc.discover(self.restaurant)
        self.assertEqual((created, updated, skipped), (1, 0, 0))
        e = LocalEvent.objects.get()
        self.assertEqual((e.source, e.provider, e.external_id, e.category, e.venue), ('auto', 'Ticketmaster', 'E1', 'festival', 'Town Ground'))
        self.assertEqual(str(e.distance_km), '3.3')
        self.assertIn('Vapi', e.address)
        self.assertIsNotNone(e.fetched_at)

    def test_rerunning_discovery_never_creates_duplicates(self):
        payload = event_payload(tm_event(), tm_event(eid='E2'))               # same real event listed under two ids
        for _ in range(2):
            with mock.patch(URLOPEN, return_value=response(payload)):
                events_svc.discover(self.restaurant)
        self.assertEqual(LocalEvent.objects.count(), 1)
        with mock.patch(URLOPEN, return_value=response(event_payload(tm_event(name='Renamed')))):
            created, updated, _ = events_svc.discover(self.restaurant)
        self.assertEqual((created, updated, LocalEvent.objects.count()), (0, 1, 1))
        self.assertEqual(LocalEvent.objects.get().name, 'Renamed')

    def test_database_blocks_duplicate_provider_ids(self):
        from django.db import IntegrityError, transaction
        kw = dict(name='X', start_at=timezone.now(), source='auto', provider='Ticketmaster', external_id='Z')
        LocalEvent.objects.create(**kw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocalEvent.objects.create(**kw)
        LocalEvent.objects.create(name='M1', start_at=timezone.now()); LocalEvent.objects.create(name='M2', start_at=timezone.now())  # blank ids are fine

    def test_no_results_message_does_not_claim_no_events_exist(self):
        with mock.patch(URLOPEN, return_value=response({})):
            msg = events_svc.refresh(self.restaurant, force=True)
        self.assertIn('no matching events', msg.lower())
        self.assertIn('other local events may exist', msg)
        self.assertEqual(LocalEvent.objects.count(), 0)

    def test_deactivated_automatic_event_stays_deactivated_after_refresh(self):
        with mock.patch(URLOPEN, return_value=response(event_payload(tm_event()))):
            events_svc.discover(self.restaurant)
        LocalEvent.objects.update(is_active=False)
        with mock.patch(URLOPEN, return_value=response(event_payload(tm_event()))):
            events_svc.discover(self.restaurant)
        self.assertFalse(LocalEvent.objects.get().is_active)

    def test_provider_failure_and_missing_key(self):
        with mock.patch(URLOPEN, side_effect=urllib.error.HTTPError('u', 429, 'x', {}, None)):
            with self.assertRaises(ProviderError) as cm:
                events_svc.discover(self.restaurant)
        self.assertTrue(cm.exception.rate_limited)
        with override_settings(EVENTS_ENABLED=False):
            with self.assertRaisesRegex(ProviderError, 'TICKETMASTER_API_KEY'):
                events_svc.discover(self.restaurant)
        self.restaurant.events_mode = 'auto'; self.restaurant.save()
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError('down')):
            page = self.as_user(self.manager).get(reverse('events'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Could not reach')

    def test_the_api_key_is_sent_only_from_the_server_never_to_the_browser(self):
        self.restaurant.events_mode = 'auto'; self.restaurant.save()
        with mock.patch(URLOPEN, return_value=response(event_payload(tm_event()))):
            page = self.as_user(self.owner).get(reverse('events'))
        self.assertNotContains(page, 'TICKETMASTER_API_KEY=')
        self.assertNotContains(page, "'k'")

    def test_manual_add_edit_deactivate_delete(self):
        c = self.as_user(self.manager)
        when = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        data = {'name': 'Village fair', 'category': 'fair', 'start_at': when, 'venue': 'Ground', 'notes': 'big'}
        self.assertRedirects(c.post(reverse('event_add'), data), reverse('events'))
        e = LocalEvent.objects.get()
        self.assertEqual((e.source, e.created_by), ('manual', self.manager))
        c.post(reverse('event_edit', args=[e.pk]), {**data, 'name': 'Village fair 2'})
        e.refresh_from_db(); self.assertEqual(e.name, 'Village fair 2')
        c.post(reverse('event_toggle', args=[e.pk])); e.refresh_from_db(); self.assertFalse(e.is_active)
        c.post(reverse('event_delete', args=[e.pk])); self.assertEqual(LocalEvent.objects.count(), 0)
        self.assertGreaterEqual(ActivityLog.objects.filter(action='event').count(), 4)

    def test_manual_duplicate_and_bad_dates_are_rejected(self):
        c = self.as_user(self.manager)
        when = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        c.post(reverse('event_add'), {'name': 'Fair', 'category': 'fair', 'start_at': when})
        page = c.post(reverse('event_add'), {'name': ' fair ', 'category': 'fair', 'start_at': when})
        self.assertContains(page, 'already in your list')
        c.post(reverse('event_add'), {'name': 'Backwards', 'category': 'fair', 'start_at': when, 'end_at': (timezone.localtime() - timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(LocalEvent.objects.count(), 1)

    def test_automatic_events_cannot_be_edited_or_deleted_but_can_be_ignored(self):
        ev = LocalEvent.objects.create(name='Auto', start_at=timezone.now(), source='auto', provider='Ticketmaster', external_id='A')
        c = self.as_user(self.manager)
        self.assertEqual(c.get(reverse('event_edit', args=[ev.pk])).status_code, 404)
        self.assertEqual(c.post(reverse('event_delete', args=[ev.pk])).status_code, 404)
        c.post(reverse('event_toggle', args=[ev.pk])); ev.refresh_from_db(); self.assertFalse(ev.is_active)

    def test_automatic_and_manual_events_are_listed_separately_with_filters(self):
        now = timezone.now() + timedelta(days=1)
        LocalEvent.objects.create(name='Hand made', start_at=now, source='manual', category='wedding')
        LocalEvent.objects.create(name='Machine found', start_at=now, source='auto', provider='Ticketmaster', external_id='A', category='music')
        page = self.as_user(self.manager).get(reverse('events'))
        self.assertContains(page, 'Entered by you'); self.assertContains(page, 'Discovered automatically')
        self.assertContains(page, 'Hand made'); self.assertContains(page, 'Machine found')
        self.assertNotContains(self.as_user(self.manager).get(reverse('events'), {'category': 'wedding'}), 'Machine found')
        self.assertNotContains(self.as_user(self.manager).get(reverse('events'), {'q': 'Machine'}), 'Hand made')

    def test_empty_states_are_honest(self):
        page = self.as_user(self.manager).get(reverse('events'))
        self.assertContains(page, 'No manual events')
        with override_settings(EVENTS_ENABLED=False):
            self.assertContains(self.as_user(self.manager).get(reverse('events')), 'not connected')

    def test_forecast_uses_events_by_mode_and_date(self):
        day = timezone.localdate() + timedelta(days=1)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=10)
        LocalEvent.objects.create(name='Manual fair', start_at=start, source='manual')
        LocalEvent.objects.create(name='Auto concert', start_at=start, source='auto', provider='Ticketmaster', external_id='A')
        LocalEvent.objects.create(name='Hidden', start_at=start, source='manual', is_active=False)
        names = lambda: sorted(e.name for e in events_svc.for_date(day, RestaurantSettings.load()))
        self.assertEqual(names(), ['Manual fair'])                            # automatic ones ignored in manual mode
        self.restaurant.events_mode = 'auto'; self.restaurant.save()
        self.assertEqual(names(), ['Auto concert', 'Manual fair'])
        self.assertEqual(events_svc.for_date(day + timedelta(days=3), RestaurantSettings.load()), [])
        page = self.as_user(self.manager).get(reverse('forecast'), {'date': day.isoformat()})
        self.assertContains(page, 'Filled from local events')

    def test_event_permissions(self):
        for user, code in ((self.owner, 200), (self.manager, 200), (self.staff, 403)):
            self.assertEqual(self.as_user(user).get(reverse('events')).status_code, code)
        ev = LocalEvent.objects.create(name='Fair', start_at=timezone.now())
        c = self.as_user(self.staff)
        for url in (reverse('event_add'), reverse('event_edit', args=[ev.pk])):
            self.assertEqual(c.get(url).status_code, 403)
        for name in ('event_toggle', 'event_delete'):
            self.assertEqual(c.post(reverse(name, args=[ev.pk])).status_code, 403)
        self.assertEqual(c.post(reverse('events_refresh')).status_code, 403)
        self.assertEqual(LocalEvent.objects.count(), 1)
