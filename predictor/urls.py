from django.urls import path

from . import auth_views, intel_views, user_views, views
from . import kitchen_views as kv

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('add-record/', views.add_record, name='add_record'),

    path('inventory/', views.inventory, name='inventory'),
    path('inventory/add/', views.food_item_add, name='food_item_add'),
    path('inventory/<int:pk>/edit/', views.food_item_edit, name='food_item_edit'),
    path('inventory/<int:pk>/toggle/', views.food_item_toggle, name='food_item_toggle'),
    path('inventory/<int:pk>/delete/', views.food_item_delete, name='food_item_delete'),

    path('forecast/', views.forecast, name='forecast'),
    path('forecast/save/', views.forecast_save, name='forecast_save'),
    path('planner/', views.planner, name='planner'),
    path('planner/approve-all/', views.planner_approve_all, name='planner_approve_all'),
    path('planner/<int:pk>/', views.planner_action, name='planner_action'),

    path('records/', views.records, name='records'),
    path('records/<int:pk>/edit/', views.record_edit, name='record_edit'),
    path('records/<int:pk>/delete/', views.record_delete, name='record_delete'),

    path('records/approvals/', views.approvals, name='approvals'),
    path('records/<int:pk>/review/', views.record_review, name='record_review'),
    path('notifications/', views.notifications, name='notifications'),
    path('activity/', views.activity_log, name='activity_log'),

    path('users/', user_views.user_list, name='user_list'),
    path('users/add/', user_views.user_add, name='user_add'),
    path('users/<int:pk>/edit/', user_views.user_edit, name='user_edit'),
    path('users/<int:pk>/toggle/', user_views.user_toggle_active, name='user_toggle_active'),
    path('users/<int:pk>/delete/', user_views.user_delete, name='user_delete'),
    path('users/<int:pk>/approve/', user_views.user_approve, name='user_approve'),
    path('users/<int:pk>/reset/', user_views.user_send_reset, name='user_send_reset'),

    path('weather/', intel_views.weather, name='weather'),
    path('weather/add/', intel_views.weather_add, name='weather_add'),
    path('weather/refresh/', intel_views.weather_refresh, name='weather_refresh'),
    path('events/', intel_views.events, name='events'),
    path('events/add/', intel_views.event_add, name='event_add'),
    path('events/refresh/', intel_views.events_refresh, name='events_refresh'),
    path('events/<int:pk>/edit/', intel_views.event_edit, name='event_edit'),
    path('events/<int:pk>/toggle/', intel_views.event_toggle, name='event_toggle'),
    path('events/<int:pk>/delete/', intel_views.event_delete, name='event_delete'),
    path('data-source/<str:which>/', intel_views.data_mode_set, name='data_mode_set'),

    path('ingredients/', kv.ingredients, name='ingredients'),
    path('ingredients/add/', kv.ingredient_add, name='ingredient_add'),
    path('ingredients/<int:pk>/', kv.ingredient_detail, name='ingredient_detail'),
    path('ingredients/<int:pk>/edit/', kv.ingredient_edit, name='ingredient_edit'),
    path('ingredients/<int:pk>/toggle/', kv.ingredient_toggle, name='ingredient_toggle'),
    path('ingredients/<int:pk>/purchase/', kv.ingredient_purchase, name='ingredient_purchase'),
    path('ingredients/<int:pk>/adjust/', kv.ingredient_adjust, name='ingredient_adjust'),
    path('stock/', kv.stock, name='stock'),
    path('recipes/', kv.recipes, name='recipes'),
    path('recipes/add/', kv.recipe_add, name='recipe_add'),
    path('recipes/<int:pk>/', kv.recipe_detail, name='recipe_detail'),
    path('recipes/<int:pk>/edit/', kv.recipe_edit, name='recipe_edit'),
    path('recipes/<int:pk>/toggle/', kv.recipe_toggle, name='recipe_toggle'),
    path('kitchen/', kv.kitchen, name='kitchen'),
    path('kitchen/new/', kv.batch_add, name='batch_add'),
    path('kitchen/consumption/', kv.consumption, name='consumption'),
    path('kitchen/<int:pk>/', kv.batch_detail, name='batch_detail'),
    path('kitchen/<int:pk>/save/', kv.batch_save, name='batch_save'),
    path('kitchen/<int:pk>/submit/', kv.batch_submit, name='batch_submit'),
    path('kitchen/<int:pk>/correct/', kv.batch_correct, name='batch_correct'),

    path('analytics/', views.analytics, name='analytics'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.reports_export, name='reports_export'),
    path('traffic/', views.traffic, name='traffic'),
    path('traffic/add/', views.traffic_add, name='traffic_add'),
    path('traffic/fetch/', views.traffic_fetch, name='traffic_fetch'),
    path('traffic/<int:pk>/delete/', views.traffic_delete, name='traffic_delete'),
    path('settings/', views.settings_view, name='settings'),

    # Authentication
    path('login/', auth_views.login_view, name='login'),
    path('signup/', auth_views.signup_view, name='signup'),
    path('logout/', auth_views.logout_view, name='logout'),
    path('password-reset/', auth_views.ForgotPasswordView.as_view(), name='password_reset'),
    path('password-reset/sent/', auth_views.ForgotPasswordDoneView.as_view(), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.ResetPasswordView.as_view(), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.ResetPasswordCompleteView.as_view(), name='password_reset_complete'),
]
