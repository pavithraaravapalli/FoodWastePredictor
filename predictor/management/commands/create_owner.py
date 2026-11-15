"""Establish the initial Owner securely (public sign-up can never create one).

    python manage.py create_owner --email owner@example.com --name "Asha Verma"   # prompts for a password
    python manage.py create_owner --promote admin                                 # make an existing user an owner
"""
import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from predictor.access import log_activity
from predictor.models import UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Create an Owner account, or promote an existing user to Owner. Passwords are hashed; none are printed.'

    def add_arguments(self, parser):
        parser.add_argument('--email')
        parser.add_argument('--name', default='')
        parser.add_argument('--promote', metavar='USERNAME', help='Make this existing user an Owner.')

    def handle(self, *args, **opts):
        if opts['promote']:
            try:
                user = User.objects.get(username=opts['promote'])
            except User.DoesNotExist:
                raise CommandError(f'No user named {opts["promote"]!r}.')
        else:
            email = (opts['email'] or '').strip().lower()
            if not email:
                raise CommandError('Give --email (or --promote USERNAME).')
            if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
                raise CommandError('An account with that email already exists. Use --promote USERNAME instead.')
            password = getpass.getpass('Password: ')
            if password != getpass.getpass('Password (again): '):
                raise CommandError('Passwords did not match.')
            try:
                validate_password(password)
            except ValidationError as e:
                raise CommandError(' '.join(e.messages))
            first, _, last = opts['name'].strip().partition(' ')
            user = User.objects.create_user(username=email, email=email, password=password, first_name=first, last_name=last)
        user.is_active = True
        user.save(update_fields=['is_active'])
        UserProfile.objects.update_or_create(user=user, defaults={'role': UserProfile.OWNER, 'is_pending': False})
        log_activity(None, 'user_role', user, new={'role': 'owner'}, summary='Owner set up from the command line',
                     object_repr=user.get_username())
        self.stdout.write(self.style.SUCCESS(f'{user.get_username()} is now an Owner.'))
