from django.conf import settings
from django.db import models

class FoodItem(models.Model):
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    cost_per_portion = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    # False = hidden from new records/forecasts but history is kept.
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        permissions = [('change_item_price', 'Can change the cost/price of food items')]

    def __str__(self):
        return self.name


class FoodRecordQuerySet(models.QuerySet):
    def counted(self):
        """Only APPROVED records feed forecasts, analytics and reports, so unreviewed
        staff entries can never skew the numbers."""
        return self.filter(status='approved')


class FoodRecord(models.Model):
    objects = FoodRecordQuerySet.as_manager()

    food_item = models.ForeignKey(
        FoodItem,
        on_delete=models.CASCADE
    )

    date = models.DateField()

    prepared_quantity = models.PositiveIntegerField()

    sold_quantity = models.PositiveIntegerField()

    weather = models.CharField(max_length=50)

    is_holiday = models.BooleanField(default=False)

    event_name = models.CharField(
        max_length=100,
        blank=True
    )

    # --- Approval workflow (added in migration 0005) -------------------------
    # Existing rows are migrated as APPROVED so history, forecasts and reports
    # are unchanged. New staff entries start as Draft/Submitted.
    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'), (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected'),
    ]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_APPROVED, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)

    class Meta:
        permissions = [('approve_foodrecord', 'Can approve or reject submitted records')]

    def save(self, *args, **kwargs):
        from django.utils import timezone
        now = timezone.now()
        if self.created_at is None and self._state.adding:
            self.created_at = now
        self.updated_at = now
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(kwargs['update_fields']) + ['updated_at']
        super().save(*args, **kwargs)

    @property
    def is_locked_for_staff(self):
        return self.status in (self.STATUS_APPROVED, self.STATUS_SUBMITTED)

    def __str__(self):
        return f"{self.food_item.name} - {self.date}"

    @property
    def wasted_quantity(self):
        return self.prepared_quantity - self.sold_quantity

    @property
    def waste_cost(self):
        return self.wasted_quantity * self.food_item.cost_per_portion

    @property
    def waste_percentage(self):
        if self.prepared_quantity == 0:
            return 0

        return (
            self.wasted_quantity /
            self.prepared_quantity
        ) * 100


class Prediction(models.Model):
    """One saved forecast + preparation plan line for an item on a date."""

    STATUS_SUGGESTED = 'suggested'
    STATUS_APPROVED = 'approved'
    STATUS_PREPARED = 'prepared'
    STATUS_CHOICES = [
        (STATUS_SUGGESTED, 'Suggested'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_PREPARED, 'Prepared'),
    ]

    food_item = models.ForeignKey(
        FoodItem,
        on_delete=models.CASCADE
    )

    date = models.DateField()

    predicted_demand = models.PositiveIntegerField()

    recommended_preparation = models.PositiveIntegerField()

    # --- added for the Preparation Planner ---------------------------------
    safety_reserve = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=40, blank=True)
    explanation = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_SUGGESTED)
    approved_quantity = models.PositiveIntegerField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'food_item__name']
        permissions = [
            ('generate_predictions', 'Can generate and save demand forecasts'),
            ('manage_plans', 'Can approve and change preparation plans'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['food_item', 'date'], name='one_prediction_per_item_per_day'),
        ]

    def __str__(self):
        return f"{self.food_item.name} - {self.date}"

    @property
    def quantity_to_prepare(self):
        """What the kitchen should make: the owner's figure once approved, else the suggestion."""
        return self.approved_quantity if self.approved_quantity is not None else self.recommended_preparation


class RestaurantSettings(models.Model):
    """Single row holding the restaurant's details and display preferences."""

    BUSINESS_TYPES = [
        ('cafe', 'Café'), ('restaurant', 'Restaurant'), ('dhaba', 'Highway dhaba'),
        ('cloud', 'Cloud kitchen'), ('canteen', 'Canteen / mess'), ('other', 'Other'),
    ]

    name = models.CharField(max_length=120, default='My Restaurant')
    business_type = models.CharField(max_length=12, choices=BUSINESS_TYPES, default='restaurant')
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    currency_symbol = models.CharField(max_length=4, default='₹')
    waste_alert_threshold = models.PositiveIntegerField(
        default=20, help_text='Waste rate (%) above which items are flagged.')
    is_configured = models.BooleanField(default=False)

    # Automatic (external API) or manual entry for each outside-world input.
    MODE_AUTO, MODE_MANUAL = 'auto', 'manual'
    MODES = [(MODE_AUTO, 'Automatic (API)'), (MODE_MANUAL, 'Manual entry')]
    weather_mode = models.CharField(max_length=6, choices=MODES, default=MODE_MANUAL)
    traffic_mode = models.CharField(max_length=6, choices=MODES, default=MODE_MANUAL)
    events_mode = models.CharField(max_length=6, choices=MODES, default=MODE_MANUAL)
    event_radius_km = models.PositiveIntegerField(default=10, help_text='Search radius for automatic event discovery.')

    class Meta:
        verbose_name_plural = 'restaurant settings'
        permissions = [
            ('view_analytics', 'Can view waste analytics and financial figures'),
            ('view_reports', 'Can view and export reports'),
            ('manage_data_sources', 'Can switch weather/traffic/event data between automatic and manual'),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def load(cls):
        """The one settings row; created from settings.py defaults the first time."""
        obj = cls.objects.order_by('pk').first()
        if obj is None:
            obj = cls.objects.create(
                name=getattr(settings, 'RESTAURANT_NAME', 'My Restaurant'),
                currency_symbol=getattr(settings, 'CURRENCY_SYMBOL', '₹'),
                waste_alert_threshold=getattr(settings, 'WASTE_ALERT_THRESHOLD', 20))
        return obj

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None


class UserProfile(models.Model):
    OWNER, MANAGER, STAFF = 'owner', 'manager', 'staff'
    ROLES = [(OWNER, 'Owner'), (MANAGER, 'Manager'), (STAFF, 'Staff')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    # Least privilege by default: anything created without an explicit role is Staff.
    role = models.CharField(max_length=10, choices=ROLES, default=STAFF)
    phone = models.CharField(max_length=30, blank=True)
    # True for self-registered sign-ups waiting for the owner to approve them.
    is_pending = models.BooleanField(default=False)

    class Meta:
        permissions = [('manage_users', 'Can manage users and assign roles')]

    def __str__(self):
        return f'{self.user} ({self.get_role_display()})'

    @classmethod
    def for_user(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        return profile


class TrafficObservation(models.Model):
    """A traffic reading near the restaurant, entered by hand or fetched from an API."""

    CONDITIONS = [
        ('free', 'Low traffic'), ('moderate', 'Moderate traffic'),
        ('heavy', 'Heavy traffic'), ('standstill', 'Very heavy traffic'),
    ]
    SOURCE_MANUAL = 'Manual entry'

    observed_at = models.DateTimeField()
    condition = models.CharField(max_length=12, choices=CONDITIONS)
    source = models.CharField(max_length=60, default=SOURCE_MANUAL)
    current_speed = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    free_flow_speed = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # Added in 0007 (all optional, so existing rows stay valid).
    is_automatic = models.BooleanField(default=False)
    road_class = models.CharField(max_length=60, blank=True)          # e.g. "Major road (FRC2)" when a provider says so
    delay_seconds = models.PositiveIntegerField(null=True, blank=True)
    congestion_pct = models.PositiveSmallIntegerField(null=True, blank=True)  # 0 = free flow, 100 = stopped
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-observed_at']

    def __str__(self):
        return f'{self.get_condition_display()} @ {self.observed_at:%d-%m-%Y %H:%M}'


class ActivityLog(models.Model):
    """Who did what, and what changed. Never stores passwords, tokens or secrets."""

    ACTIONS = [
        ('record_create', 'Record created'), ('record_update', 'Record updated'),
        ('record_submit', 'Record submitted'), ('record_approve', 'Record approved'),
        ('record_reject', 'Record rejected'), ('record_delete', 'Record deleted'),
        ('item_create', 'Food item created'), ('item_update', 'Food item updated'),
        ('item_toggle', 'Food item activated/deactivated'), ('item_delete', 'Food item deleted'),
        ('prediction', 'Forecast generated'), ('plan_update', 'Preparation plan changed'),
        ('user_create', 'User created'), ('user_update', 'User updated'),
        ('user_role', 'Role changed'), ('user_deactivate', 'User deactivated'),
        ('user_reactivate', 'User reactivated'), ('user_approve', 'Sign-up approved'),
        ('user_reset', 'Password reset sent'), ('settings', 'Restaurant settings changed'),
        ('permission', 'Permissions changed'),
        ('ingredient', 'Ingredient changed'), ('recipe', 'Recipe changed'), ('stock', 'Stock changed'),
        ('batch_create', 'Preparation batch created'), ('batch_submit', 'Preparation batch submitted'),
        ('batch_review', 'Preparation batch reviewed'),
        ('weather', 'Weather entered'), ('event', 'Event changed'), ('traffic', 'Traffic entered'),
        ('ingredient', 'Ingredient changed'), ('recipe', 'Recipe changed'), ('stock', 'Stock changed'),
        ('batch', 'Preparation batch'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    user_label = models.CharField(max_length=150, blank=True)  # kept even if the user is later removed
    action = models.CharField(max_length=20, choices=ACTIONS, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.created_at:%d-%m-%Y %H:%M} {self.user_label} {self.get_action_display()}'


class WeatherObservation(models.Model):
    """One weather reading or forecast. Rows are only ever added, never overwritten, so history is kept."""

    KIND_CURRENT, KIND_FORECAST = 'current', 'forecast'
    KINDS = [(KIND_CURRENT, 'Current conditions'), (KIND_FORECAST, 'Forecast for a day')]
    CONDITIONS = [('sunny', 'Sunny'), ('cloudy', 'Cloudy'), ('rainy', 'Rainy'), ('stormy', 'Stormy'), ('other', 'Other')]
    SOURCE_AUTO, SOURCE_MANUAL = 'auto', 'manual'
    SOURCES = [(SOURCE_AUTO, 'Automatic'), (SOURCE_MANUAL, 'Manual')]

    kind = models.CharField(max_length=10, choices=KINDS, default=KIND_CURRENT)
    source = models.CharField(max_length=6, choices=SOURCES, default=SOURCE_MANUAL, db_index=True)
    provider = models.CharField(max_length=60, blank=True)               # e.g. "Open-Meteo"; blank for manual
    target_date = models.DateField(db_index=True)                        # the day the reading/forecast is about (local date)
    observed_at = models.DateTimeField()                                 # when the reading applies / was entered
    fetched_at = models.DateTimeField(null=True, blank=True)             # when we asked the provider
    condition = models.CharField(max_length=10, choices=CONDITIONS, default='other')
    weather_code = models.PositiveSmallIntegerField(null=True, blank=True)   # WMO code from the provider
    temperature_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    temp_min_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    temp_max_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    precipitation_mm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    precipitation_probability = models.PositiveSmallIntegerField(null=True, blank=True)
    humidity_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    wind_kmh = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-observed_at', '-id']
        indexes = [models.Index(fields=['kind', 'target_date', '-observed_at'])]

    def __str__(self):
        return f'{self.get_condition_display()} on {self.target_date:%d-%m-%Y} ({self.get_source_display()})'

    @property
    def is_automatic(self):
        return self.source == self.SOURCE_AUTO


class LocalEvent(models.Model):
    """A nearby event that may change demand: discovered through an API or entered by hand."""

    CATEGORIES = [
        ('festival', 'Local festival'), ('fair', 'Fair / market'), ('wedding', 'Wedding'),
        ('college', 'College event'), ('religious', 'Religious event'), ('community', 'Community gathering'),
        ('sports', 'Sports'), ('music', 'Music / concert'), ('other', 'Other'),
    ]
    SOURCE_AUTO, SOURCE_MANUAL = 'auto', 'manual'
    SOURCES = [(SOURCE_AUTO, 'Automatic'), (SOURCE_MANUAL, 'Manual')]

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=12, choices=CATEGORIES, default='other')
    venue = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=255, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=6, choices=SOURCES, default=SOURCE_MANUAL, db_index=True)
    provider = models.CharField(max_length=60, blank=True)
    external_id = models.CharField(max_length=100, blank=True)           # the provider's event id (automatic events)
    url = models.URLField(blank=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)                        # soft deactivation of irrelevant events
    fetched_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_at', 'name']
        constraints = [
            # Re-running discovery can never create the same provider event twice.
            models.UniqueConstraint(fields=['provider', 'external_id'], condition=~models.Q(external_id=''),
                                    name='unique_provider_event'),
        ]

    def __str__(self):
        return f'{self.name} ({self.start_at:%d-%m-%Y})'

    @property
    def is_automatic(self):
        return self.source == self.SOURCE_AUTO


# ============================================================================
# Ingredients, recipes, stock and kitchen consumption (migration 0009)
# ============================================================================
from decimal import Decimal as _D  # noqa: E402
from .services import units as _units  # noqa: E402


class Ingredient(models.Model):
    """A raw ingredient. Stock and cost are kept in the BASE unit (g, ml or pcs)."""
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=60, blank=True)
    base_unit = models.CharField(max_length=3, choices=_units.BASE_CHOICES, default='g')
    current_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    min_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    cost_per_unit = models.DecimalField('Purchase cost per base unit', max_digits=12, decimal_places=4, default=0)
    supplier = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_low(self):
        return self.min_stock > 0 and self.current_stock <= self.min_stock

    @property
    def stock_status(self):
        if self.current_stock <= 0:
            return 'out'
        return 'low' if self.is_low else 'ok'

    @property
    def stock_value(self):
        return self.current_stock * self.cost_per_unit

    def pretty_stock(self):
        return _units.pretty(self.current_stock, self.base_unit)

    def pretty_min(self):
        return _units.pretty(self.min_stock, self.base_unit)


class Recipe(models.Model):
    food_item = models.OneToOneField(FoodItem, on_delete=models.CASCADE, related_name='recipe')
    name = models.CharField(max_length=120)
    yield_quantity = models.DecimalField('Portions this recipe makes', max_digits=10, decimal_places=2, default=1)
    instructions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['food_item__name']

    def __str__(self):
        return self.name

    def lines_with_cost(self):
        """Per-portion quantities and cost, with each line's preparation loss included."""
        rows = []
        for line in self.lines.select_related('ingredient'):
            per_portion = line.base_quantity_per_portion
            rows.append((line, per_portion, per_portion * line.ingredient.cost_per_unit))
        return rows

    @property
    def cost_per_portion(self):
        return sum((c for _, _, c in self.lines_with_cost()), _D('0'))


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='lines')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name='recipe_lines')
    quantity = models.DecimalField('Quantity for the whole recipe', max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=3, choices=_units.UNIT_CHOICES, default='g')
    loss_percent = models.DecimalField('Preparation loss %', max_digits=5, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['recipe', 'ingredient'], name='one_line_per_ingredient_in_recipe')]
        ordering = ['id']

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ingredient_id and self.unit and not _units.compatible(self.unit, self.ingredient.base_unit):
            raise ValidationError(f'{self.ingredient.name} is measured in {self.ingredient.get_base_unit_display()}; '
                                  f'{self.get_unit_display()} cannot be used for it.')

    @property
    def base_quantity_per_portion(self):
        """Ingredient needed (base unit) for ONE portion: qty / yield, grossed up for preparation loss."""
        base = _units.to_base(self.quantity, self.unit)
        loss = min(self.loss_percent or _D('0'), _D('95'))
        gross = base / (_D('1') - loss / _D('100'))
        return gross / (self.recipe.yield_quantity or _D('1'))


class StockTransaction(models.Model):
    """Append-only stock history. Rows are never edited or deleted; mistakes are fixed with reversals."""
    PURCHASE, ADJUSTMENT, CONSUMPTION, REVERSAL, OPENING = 'purchase', 'adjustment', 'consumption', 'reversal', 'opening'
    KINDS = [(PURCHASE, 'Purchase'), (ADJUSTMENT, 'Adjustment'), (CONSUMPTION, 'Kitchen consumption'),
             (REVERSAL, 'Reversal'), (OPENING, 'Opening stock')]

    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name='transactions')
    kind = models.CharField(max_length=12, choices=KINDS, db_index=True)
    quantity_change = models.DecimalField(max_digits=14, decimal_places=3)      # signed, base unit
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    reference = models.CharField(max_length=80, unique=True)                     # prevents duplicate deductions
    reason = models.CharField(max_length=300, blank=True)
    batch = models.ForeignKey('PreparationBatch', null=True, blank=True, on_delete=models.PROTECT, related_name='transactions')
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_by')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        permissions = [('manage_stock', 'Can record purchases and adjust ingredient stock')]

    def __str__(self):
        return f'{self.reference}: {self.quantity_change} {self.ingredient.base_unit} {self.ingredient.name}'


class PreparationBatch(models.Model):
    PLANNED, SUBMITTED, CORRECTION = 'planned', 'submitted', 'correction'
    STATUSES = [(PLANNED, 'Planned'), (SUBMITTED, 'Submitted'), (CORRECTION, 'Needs correction')]

    food_item = models.ForeignKey(FoodItem, on_delete=models.PROTECT, related_name='batches')
    plan = models.ForeignKey(Prediction, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    date = models.DateField(db_index=True)
    time_slot = models.CharField(max_length=40, blank=True)
    planned_quantity = models.PositiveIntegerField(default=0)
    actual_quantity = models.PositiveIntegerField(null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_batches')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=12, choices=STATUSES, default=PLANNED, db_index=True)
    notes = models.CharField(max_length=300, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    submission_count = models.PositiveIntegerField(default=0)     # makes every deduction reference unique
    correction_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        permissions = [
            ('record_consumption', 'Can record actual ingredient usage for assigned batches'),
            ('review_consumption', 'Can review submitted batches and request corrections'),
        ]

    def __str__(self):
        return f'{self.food_item.name} batch {self.date:%d-%m-%Y}'

    @property
    def is_submitted(self):
        return self.status == self.SUBMITTED


class IngredientUsage(models.Model):
    batch = models.ForeignKey(PreparationBatch, on_delete=models.CASCADE, related_name='usages')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name='usages')
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)      # base unit
    actual_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    waste_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=3, choices=_units.BASE_CHOICES, default='g')            # base unit of the ingredient
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'ingredient'], name='one_usage_per_ingredient_in_batch')]
        ordering = ['ingredient__name']

    @property
    def difference(self):
        return None if self.actual_quantity is None else self.actual_quantity - self.expected_quantity
