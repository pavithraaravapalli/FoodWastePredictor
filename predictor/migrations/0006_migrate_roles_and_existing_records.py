"""Data migration: map old roles onto Owner/Manager/Staff. Reversible and non-destructive.

* 'kitchen' and 'other' -> 'staff'. Existing 'owner' / 'manager' values are kept as they are.
* Every existing superuser becomes an Owner (this is how the initial owner is established
  on a database that already has an admin account).
* Existing FoodRecord rows were given status 'approved' by 0005's default, so history is untouched.
"""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    UserProfile = apps.get_model('predictor', 'UserProfile')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    UserProfile.objects.filter(role__in=['kitchen', 'other']).update(role='staff')
    for user in User.objects.filter(is_superuser=True):
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'role': 'owner'})
        if profile.role != 'owner':
            profile.role = 'owner'
            profile.save(update_fields=['role'])


def backwards(apps, schema_editor):
    # Old role names ('kitchen'/'other') cannot be restored; 'staff' is left as is. Nothing is lost.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('predictor', '0005_roles_workflow_activitylog'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
