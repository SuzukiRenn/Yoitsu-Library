"""Append missing, reviewed ranked novels; never generate reader activity."""
import hashlib
import json
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from booklist import rankings
from booklist.models import Work, WorkAlias
from booklist.catalog import search_key


def import_catalog(data=None,apply=False):
    data=data or rankings.dataset()
    created=[]
    with transaction.atomic():
        existing=rankings.catalog_bindings(data)
        pending=[(key,row) for key,row in data['works'].items() if key not in existing]
        for key,row in pending:
            if any(row['refs'].values()):raise CommandError('Referenced existing catalog item missing: '+key)
        if not apply:return {'would_create':len(pending),'existing':len(existing)}
        snapshots={}
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names():
                cursor.execute(f'SELECT max(rowid) FROM "{table}"')
                limit=cursor.fetchone()[0] or 0
                cursor.execute(f'SELECT * FROM "{table}" WHERE rowid<=%s ORDER BY rowid',[limit])
                snapshots[table]=(limit,hashlib.sha256(repr(cursor.fetchall()).encode()).hexdigest())
        for key,row in pending:
            refs=row['refs']
            if any(refs.values()):raise CommandError('Referenced existing catalog item missing: '+key)
            if row['edition']!='book' or not rankings.curated_cover(row['cover']):raise CommandError('Unverified ranked novel: '+key)
            work=Work.objects.create(original_submitted_title=row['title'],author_override=row['author'],author_overridden=True,
                cover_url_override=row['cover'],cover_url_overridden=True,
                source_metadata={'source_provider':'konorano_rankings','ranking_key':key,'japanese_title':row['japanese'],
                    'edition':'book','publisher_category':row['publisher'],'source_url':row['source_page']})
            for alias in dict.fromkeys(row['aliases']):
                if alias and alias!=work.display_title:WorkAlias.objects.create(work=work,alias=alias,normalized_alias=search_key(alias))
            created.append(work.pk)
        with connection.cursor() as cursor:
            for table,(limit,digest) in snapshots.items():
                cursor.execute(f'SELECT * FROM "{table}" WHERE rowid<=%s ORDER BY rowid',[limit])
                if hashlib.sha256(repr(cursor.fetchall()).encode()).hexdigest()!=digest:raise CommandError('Existing rows changed: '+table)
            if cursor.execute('PRAGMA foreign_key_check').fetchall():raise CommandError('Foreign key check failed')
        if len(rankings.catalog_bindings(data))!=len(data['works']):raise CommandError('Incomplete ranked catalog')
    return {'success':True,'created_works':len(created),'existing':len(existing),'preexisting_rows_unchanged':True,'created_ids':created}

class Command(BaseCommand):
    help=__doc__
    def add_arguments(self,parser):parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        result=import_catalog(apply=options['apply'])
        self.stdout.write('RANKINGS_CATALOG_SUCCESS '+json.dumps(result))
