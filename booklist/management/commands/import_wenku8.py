import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from booklist.importing import create_batch, resume_batch, run_batch
from booklist.models import ImportBatch
from integrations.wenku8.offline import SnapshotError


class Command(BaseCommand):
    help='Import a bounded local HTML manifest only. Never connects to Wenku8.'

    def add_arguments(self,parser):
        group=parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--manifest',type=Path)
        group.add_argument('--resume')
        parser.add_argument('--retry-failed',action='store_true')
        parser.add_argument('--max-items',type=int,default=20)

    def handle(self,*args,**options):
        try:
            if options['retry_failed'] and not options['resume']:
                raise SnapshotError('retry_requires_resume')
            if not 1<=options['max_items']<=20:
                raise SnapshotError('max_items_must_be_1_to_20')
            batch=resume_batch(options['resume']) if options['resume'] else create_batch(options['manifest'])
            summary=run_batch(batch,options['max_items'],options['retry_failed'])
        except (SnapshotError,OSError,ValueError,ImportBatch.DoesNotExist,ValidationError,DatabaseError) as exc:
            raise CommandError(str(exc) if isinstance(exc,SnapshotError) else 'Invalid or unreadable batch input') from None
        self.stdout.write(json.dumps(summary,ensure_ascii=False,indent=2))
        if summary['counts']['failed']:
            raise CommandError('Batch retained failed items; inspect the result, fix input in a new batch or explicitly retry unchanged failed items.')
