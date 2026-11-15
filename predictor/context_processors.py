"""Template context shared by every page: restaurant info and sidebar navigation."""
from django.conf import settings

from .access import role_of
from .models import RestaurantSettings, UserProfile

# One place to define the sidebar. A `url_name` of None would render as a
# disabled "Soon" item; all pages are built now.
# `perm` hides an item from people who lack the permission. This is ONLY a convenience:
# every one of these pages enforces the same permission again on the server.
NAV_GROUP_HEADINGS = [
    ('main', 'Main'),
    ('operations', 'Operations'),
    ('kitchen_stock', 'Kitchen and Stock'),
    ('intelligence', 'Intelligence'),
    ('management', 'Management'),
]

NAV_ITEMS = [
    # MAIN
    {'label': 'Overview', 'icon': 'layout-dashboard', 'url_name': 'dashboard', 'group': 'main'},
    {'label': 'Demand Forecast', 'icon': 'trending-up', 'url_name': 'forecast', 'perm': 'generate_predictions', 'group': 'main'},
    {'label': 'Notifications', 'icon': 'bell', 'url_name': 'notifications', 'badge': 'notices', 'group': 'main'},

    # OPERATIONS
    {'label': 'Food Inventory', 'staff_label': 'Food Items', 'icon': 'package', 'url_name': 'inventory',
     'also': ['food_item_add', 'food_item_edit'], 'perm': 'view_fooditem', 'group': 'operations'},
    {'label': 'Daily Records', 'staff_label': 'My Records', 'icon': 'clipboard-list', 'url_name': 'records',
     'also': ['add_record', 'record_edit'], 'perm': 'view_foodrecord', 'group': 'operations'},
    {'label': 'Approvals', 'icon': 'badge-check', 'url_name': 'approvals', 'perm': 'approve_foodrecord', 'badge': 'pending', 'group': 'operations'},
    {'label': 'Preparation Planner', 'staff_label': "Today's Plan", 'icon': 'chef-hat', 'url_name': 'planner', 'perm': 'view_prediction', 'group': 'operations'},

    # KITCHEN AND STOCK
    {'label': 'Ingredients', 'icon': 'carrot', 'url_name': 'ingredients', 'also': ['ingredient_add', 'ingredient_edit', 'ingredient_detail'], 'perm': 'view_ingredient', 'group': 'kitchen_stock'},
    {'label': 'Stock', 'icon': 'warehouse', 'url_name': 'stock', 'perm': 'view_ingredient', 'group': 'kitchen_stock'},
    {'label': 'Recipes', 'icon': 'book-open', 'url_name': 'recipes', 'also': ['recipe_add', 'recipe_edit', 'recipe_detail'], 'perm': 'view_recipe', 'group': 'kitchen_stock'},
    {'label': 'Kitchen', 'staff_label': 'My Batches', 'icon': 'cooking-pot', 'url_name': 'kitchen', 'also': ['batch_add', 'batch_detail', 'consumption'], 'perm': 'view_preparationbatch', 'group': 'kitchen_stock'},

    # INTELLIGENCE
    {'label': 'Weather', 'icon': 'cloud-sun', 'url_name': 'weather', 'perm': 'view_weatherobservation', 'group': 'intelligence'},
    {'label': 'Traffic Intelligence', 'icon': 'route', 'url_name': 'traffic', 'perm': 'view_trafficobservation', 'group': 'intelligence'},
    {'label': 'Local Events', 'icon': 'calendar-days', 'url_name': 'events', 'also': ['event_add', 'event_edit'], 'perm': 'view_localevent', 'group': 'intelligence'},

    # MANAGEMENT
    {'label': 'Waste Analytics', 'icon': 'leaf', 'url_name': 'analytics', 'perm': 'view_analytics', 'group': 'management'},
    {'label': 'Reports', 'icon': 'file-bar-chart', 'url_name': 'reports', 'perm': 'view_reports', 'group': 'management'},
    {'label': 'Team', 'icon': 'users-round', 'url_name': 'user_list', 'also': ['user_add', 'user_edit'], 'perm': 'manage_users', 'group': 'management'},
    {'label': 'Activity Log', 'icon': 'history', 'url_name': 'activity_log', 'perm': 'view_activitylog', 'group': 'management'},
    {'label': 'Settings', 'icon': 'settings', 'url_name': 'settings', 'group': 'management'},
]


def _counts(user):
    from .models import FoodRecord, UserProfile
    pending = notices = 0
    if user.has_perm('predictor.approve_foodrecord'):
        pending = FoodRecord.objects.filter(status='submitted').count()
    mine = FoodRecord.objects.filter(created_by=user, status='rejected').count()
    notices = pending + mine
    if user.has_perm('predictor.manage_users'):
        notices += UserProfile.objects.filter(is_pending=True).count()
    return pending, notices


def app_shell(request):
    match = getattr(request, 'resolver_match', None)
    current = match.url_name if match else None

    user = getattr(request, 'user', None)
    authed = user is not None and user.is_authenticated
    role = role_of(user) if authed else None
    pending, notices = _counts(user) if authed else (0, 0)
    nav = []
    for item in NAV_ITEMS:
        if authed and item.get('perm') and not user.has_perm(f"predictor.{item['perm']}"):
            continue
        names = [item['url_name']] + item.get('also', [])
        label = item.get('staff_label') if role == 'staff' and item.get('staff_label') else item['label']
        count = {'pending': pending, 'notices': notices}.get(item.get('badge'), 0)
        nav.append({**item, 'label': label, 'count': count, 'active': current in names and current is not None})

    groups = {gid: {'id': gid, 'heading': heading, 'items': [], 'has_active': False}
              for gid, heading in NAV_GROUP_HEADINGS}
    for item in nav:
        gid = item.get('group', 'main')
        if gid in groups:
            groups[gid]['items'].append(item)
            if item.get('active'):
                groups[gid]['has_active'] = True

    nav_groups = [g for g in groups.values() if g['items']]

    restaurant = RestaurantSettings.load()
    display_name, role_label = '', ''
    if authed:
        display_name = user.get_full_name() or user.get_username()
        role_label = dict(UserProfile.ROLES).get(role, '')

    return {
        'restaurant': restaurant,
        'restaurant_name': restaurant.name,
        'user_role': role_label, 'role_key': role, 'pending_count': pending, 'notice_count': notices,
        'debug': settings.DEBUG,
        'email_configured': settings.EMAIL_CONFIGURED,
        'currency': restaurant.currency_symbol,
        'nav_items': nav,
        'nav_groups': nav_groups,
        'display_name': display_name,
        'user_initial': (display_name[:1] or 'G').upper(),
    }
