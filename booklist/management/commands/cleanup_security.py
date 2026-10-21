from django.core.management.base import BaseCommand
from django.utils import timezone
from booklist.models import RateLimitBucket, AdminSession, IdempotencyRecord, AccountSession


class Command(BaseCommand):
    help = 'Remove expired security buckets/sessions/idempotency; never remove anonymous contributions.'

    def handle(self, *args, **options):
        now = timezone.now()
        for model in (RateLimitBucket, AdminSession, AccountSession, IdempotencyRecord):
            count, _ = model.objects.filter(expires_at__lte=now).delete()
            self.stdout.write(f'{model.__name__}: {count}')
