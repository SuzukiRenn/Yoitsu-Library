from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from django.conf import settings
from django.db import OperationalError, connection
from django.test import TestCase, TransactionTestCase, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from booklist import activities, services, public_booklist as books
from booklist.models import Activity, Work
from booklist.read_rate_limit import consume


class ReadCounterTests(SimpleTestCase):
    def test_counter_is_shared_and_atomic_across_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'limits.sqlite3'
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _:consume(path,'same-key',5,200,100),range(16)))
            self.assertEqual(sum(results),5)
            self.assertFalse(consume(path,'same-key',5,200,100))
            self.assertTrue(consume(path,'different-slot',5,201,101))
            consume(path,'cleanup',5,400,300)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM read_limits WHERE key='same-key'").fetchone()[0],0)

    def test_storage_failure_does_not_disable_throttling(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OperationalError):
                consume(Path(folder)/'missing'/'limits.sqlite3','key',1,200,100)


class CatalogContentionTests(TransactionTestCase):
    def _fixture_teardown(self):
        # The normal database trigger forbids deleting the last admin, including test flushes.
        with connection.cursor() as cursor:
            triggers=cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name,_ in triggers:cursor.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        try:super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _,sql in triggers:cursor.execute(sql)

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.setting=self.settings(READ_RATE_LIMIT_DATABASE=Path(self.folder.name)/'reads.sqlite3')
        self.setting.enable();self.addCleanup(self.setting.disable)
        token,self.actor=services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE]=token
        admin=services.initialize_admin('lockowner','Database-Contention!Password-49')
        now=timezone.now()
        self.activity=Activity.objects.create(title='并发读验收',status='active',start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1),created_by=admin)
        self.work=Work.objects.create(original_submitted_title='并发详情作品',public_added_at=now)
        activities.submit(self.activity.pk,self.actor,{'work_id':self.work.pk,'title':self.work.display_title,
            'reason':'可读取的理由','confirmed_unmatched':False,'copy_public_reason':True})

    def test_catalog_pages_and_polling_read_while_importer_holds_write_lock(self):
        writer=sqlite3.connect(connection.settings_dict['NAME'],timeout=1,isolation_level=None)
        try:
            writer.execute('BEGIN IMMEDIATE')
            for path in ['/',f'/works/{self.work.pk}/',f'/activities/{self.activity.pk}/',
                         f'/works/{self.work.pk}/?activity={self.activity.pk}',
                         f'/api/v1/public/stats/?ids={self.work.pk}',
                         f'/api/v1/activities/{self.activity.pk}/stats/?ids={self.work.pk}']:
                response=self.client.get(path)
                self.assertEqual(response.status_code,200,path)
        finally:
            writer.rollback();writer.close()

    def test_no_due_activity_never_opens_a_write_transaction(self):
        with CaptureQueriesContext(connection) as queries:
            activities.advance_due()
        self.assertFalse(any('BEGIN' in item['sql'] for item in queries))

    def test_due_activity_still_transitions_in_a_transaction(self):
        self.activity.status='scheduled';self.activity.started_at=None;self.activity.save()
        with CaptureQueriesContext(connection) as queries:
            activities.advance_due()
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.status,'active')
        self.assertTrue(any('BEGIN' in item['sql'] for item in queries))

    def test_read_limit_remains_enforced_in_separate_storage(self):
        services.rate_limit('catalog_read','limited-test',1,60)
        with self.assertRaises(services.DomainError) as caught:
            services.rate_limit('catalog_read','limited-test',1,60)
        self.assertEqual(caught.exception.status,429)


class DatabaseFailurePageTests(TestCase):
    def test_browser_failure_is_html_but_api_protocol_stays_json(self):
        with patch('booklist.public_views.read_limit',side_effect=OperationalError('database is locked')):
            with self.assertLogs('booklist.views',level='WARNING') as logs:
                page=self.client.get('/')
                api=self.client.get('/api/v1/public/works/')
        self.assertContains(page,'暂时无法加载',status_code=503)
        self.assertContains(page,'重新加载',status_code=503)
        self.assertEqual(page['Retry-After'],'3')
        self.assertTrue(page['Content-Type'].startswith('text/html'))
        self.assertNotContains(page,'database is locked',status_code=503)
        self.assertEqual(api.json()['error']['code'],'database_unavailable')
        self.assertIn('database is locked',' '.join(logs.output))

    def test_identity_query_failure_uses_same_browser_fallback(self):
        token,_=services.issue_anonymous();self.client.cookies[settings.ANONYMOUS_COOKIE]=token
        with patch('booklist.middleware.AnonymousClient.objects.filter',side_effect=OperationalError('database is locked')):
            with self.assertLogs('booklist.views',level='WARNING'):
                response=self.client.get('/')
        self.assertContains(response,'重新加载',status_code=503)
        self.assertEqual(response['X-Frame-Options'],'DENY')
