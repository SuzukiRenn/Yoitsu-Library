import getpass
import os
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from booklist.services import reset_admin_password, DomainError


class Command(BaseCommand):
    help = 'Server-only password recovery, revokes all old sessions; never enables a disabled account.'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        password = os.environ.get('BOOKLIST_RESET_PASSWORD') or getpass.getpass('New admin password: ')
        try:
            reset_admin_password(options['username'], password)
        except (ValidationError, DomainError):
            raise CommandError('Account not found or password rejected; no changes.') from None
        self.stdout.write('Password reset; all old sessions invalidated. Account status unchanged.')
