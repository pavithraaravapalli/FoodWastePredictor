"""Fill the database with realistic SAMPLE data so charts, forecasts and reports have something to show."""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from predictor.models import FoodItem, FoodRecord

# name, category, cost per portion, typical weekday sales (Mon..Sun multiplier applied)
MENU = [
    ('Veg Thali', 'Main Course', 60, 55),
    ('Chicken Biryani', 'Main Course', 85, 40),
    ('Masala Dosa', 'Breakfast', 35, 45),
    ('Paneer Wrap', 'Snacks', 45, 35),
    ('Filter Coffee', 'Beverages', 12, 70),
]
WEEKDAY = [0.8, 0.85, 0.9, 1.0, 1.25, 1.5, 1.3]       # Mon..Sun: busier at the weekend


class Command(BaseCommand):
    help = 'Create sample food items and daily records (for demos). Refuses to run on a database that already has records.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=56, help='How many past days to generate (default 56).')
        parser.add_argument('--force', action='store_true', help='Run even if records already exist (only fills missing item/date rows).')
        parser.add_argument('--seed', type=int, default=7)

    def handle(self, *args, **opts):
        if FoodRecord.objects.exists() and not opts['force']:
            raise CommandError('This database already has records. Re-run with --force to add sample rows anyway.')
        random.seed(opts['seed'])
        today = timezone.localdate()
        items = []
        for name, cat, cost, _ in MENU:
            item, _created = FoodItem.objects.get_or_create(name=name, defaults={'category': cat, 'cost_per_portion': cost})
            items.append(item)
        made = 0
        for back in range(1, opts['days'] + 1):
            day = today - timedelta(days=back)
            rainy = random.random() < 0.2
            for item, (_, _, _, base) in zip(items, MENU):
                if FoodRecord.objects.filter(food_item=item, date=day).exists():
                    continue
                mean = base * WEEKDAY[day.weekday()] * (0.8 if rainy else 1.0)
                sold = max(int(random.gauss(mean, mean * 0.07)), 3)
                prepared = int(sold * random.uniform(1.0, 1.25)) + 1
                FoodRecord.objects.create(food_item=item, date=day, prepared_quantity=prepared, sold_quantity=sold,
                                          weather='Rainy' if rainy else 'Sunny', is_holiday=False, event_name='')
                made += 1
        self.stdout.write(self.style.SUCCESS(
            f'Created {made} sample records over {opts["days"]} days for {len(items)} items. '
            'This is SAMPLE data: delete it from Daily Records / Food Inventory before real use.'))
