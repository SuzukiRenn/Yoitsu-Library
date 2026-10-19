from django.core.management.base import BaseCommand
from booklist.activities import advance_due


class Command(BaseCommand):
    help = 'Apply due activity transitions without extending participation windows.'

    def handle(self, *args, **options):
        advance_due()
        self.stdout.write('Activity transitions processed')
