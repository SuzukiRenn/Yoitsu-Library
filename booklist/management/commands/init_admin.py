import getpass
import os
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from booklist.models import Admin
from booklist.services import initialize_admin, DomainError


class Command(BaseCommand):
    help = 'Create only the first admin; password is prompted or read from BOOKLIST_INITIAL_PASSWORD.'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        if Admin.objects.exists():
            self.stdout.write('Already initialized; no changes.')
            return
        password = os.environ.get('BOOKLIST_INITIAL_PASSWORD') or getpass.getpass('Initial admin password: ')
        try:
            account = initialize_admin(options['username'], password)
        except (ValidationError, DomainError):
            raise CommandError('Invalid username or password; no changes.') from None
        self.stdout.write('Administrator initialized.' if account else 'Already initialized; no changes.')
