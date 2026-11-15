"""LOCAL DEVELOPMENT ONLY: create one Owner, Manager and Staff account with random passwords.

    python manage.py create_dev_users

Passwords are generated fresh each run, printed once, and stored only as hashes.
The command refuses to run when DEBUG is off. Never run it against a real restaurant's database.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string

from predictor.models import UserProfile

User = get_user_model()
ACCOUNTS = [('dev-owner', UserProfile.OWNER), ('dev-manager', UserProfile.MANAGER), ('dev-staff', UserProfile.STAFF)]


class Command(BaseCommand):
    help = 'Create dev-owner / dev-manager / dev-staff with random passwords (DEBUG only).'

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError('Refusing to create development accounts while DEBUG is off.')
        self.stdout.write('Development accounts (passwords are shown only now):\n')
        for username, role in ACCOUNTS:
            password = get_random_string(14, 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789')
            user, created = User.objects.get_or_create(username=username, defaults={'email': f'{username}@example.test'})
            user.set_password(password)
            user.is_active, user.is_staff, user.is_superuser = True, False, False
            user.first_name = username.replace('dev-', '').title()
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'role': role, 'is_pending': False})
            self.stdout.write(f'  {role:<8} username: {username:<12} password: {password}')
        self.stdout.write('\nSign in at /login/. Delete these users before using the database for real.')
