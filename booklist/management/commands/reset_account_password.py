import getpass
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib.auth.hashers import make_password
from booklist import accounts, services
from booklist.models import Account, AccountSession


class Command(BaseCommand):
    help = 'Operator-only reader recovery. Prompts for password; revokes every session; never re-enables.'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        secret = getpass.getpass('New reader password: ')
        if secret != getpass.getpass('Confirm password: '):
            raise CommandError('Passwords do not match')
        try:
            accounts.password(secret)
            encoded = make_password(secret)
            with transaction.atomic():
                row = Account.objects.get(username=accounts.username(options['username']))
                row.password_hash = encoded
                row.session_version += 1
                row.save(update_fields=['password_hash', 'session_version', 'updated_at'])
                AccountSession.objects.filter(account=row).delete()
                services.audit(None, 'account.password_reset', row, reason='server_operator_recovery')
        except (Account.DoesNotExist, ValidationError, services.DomainError):
            raise CommandError('Account not found or password rejected; no changes.') from None
        self.stdout.write('Password reset. Sessions revoked. Account status unchanged.')
