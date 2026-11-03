import importlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.conf import settings
from django.db import connection, OperationalError
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from booklist import accounts, activities, merging, public_booklist as books, services
from booklist.models import Activity, Notification, PublicRecommendation, Work, WorkLikeEvent


@override_settings(REQUIRE_ACCOUNT=True)
class NotificationRetractionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.people = {}; cls.tokens = {}
        for name in ('alice', 'bob', 'carol'):
            token, session = accounts.register(name, name, 'Retraction-Testing!2026')
            cls.people[name] = session.account; cls.tokens[name] = token
        cls.work = Work.objects.create(original_submitted_title='撤回测试', public_added_at=timezone.now())
        cls.admin = services.initialize_admin('retractadmin', 'Retraction-Admin!2026')
        now = timezone.now()
        cls.activity = Activity.objects.create(title='独立测试主题', status='active', created_by=cls.admin,
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1))

    def browser(self, name):
        client = Client(enforce_csrf_checks=True)
        client.cookies[settings.ACCOUNT_COOKIE] = self.tokens[name]
        return client

    def recommend(self, name, activity=False):
        if activity:
            return activities.submit(self.activity.pk, self.people[name].principal, {
                'work_id': self.work.pk, 'title': self.work.display_title, 'reason': '', 'copy_public_reason': False})
        return books.set_relation(self.work, self.people[name].principal, 'recommend', True)

    def post(self, client, path, data):
        csrf = client.get('/api/v1/csrf/').json()['csrf_token']
        return client.post(path, json.dumps(data), content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

    def test_public_withdraw_retracts_html_api_unread_target_and_read_actions(self):
        self.recommend('alice'); self.recommend('bob')
        note = Notification.objects.get(); client = self.browser('alice')
        self.assertEqual(client.get('/api/v1/notifications/unread/').json()['unread_count'], 1)
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        note.refresh_from_db(); self.assertIsNotNone(note.retracted_at); self.assertIsNone(note.read_at)
        self.assertEqual(client.get('/api/v1/notifications/').json(), {'unread_count': 0, 'total': 0, 'has_more': False, 'results': []})
        page = client.get('/notifications/')
        self.assertNotContains(page, '也推荐了你推荐的'); self.assertContains(page, '暂无消息')
        self.assertEqual(page.context['unread_notifications'], 0)
        self.assertEqual(client.get(f'/notifications/{note.pk}/work/').status_code, 404)
        self.assertEqual(self.post(client, '/api/v1/notifications/read/', {'ids': [note.pk]}).json()['changed'], 0)
        self.assertEqual(self.post(client, '/api/v1/notifications/read-all/', {}).json()['changed'], 0)
        self.assertEqual(books.recommendations().filter(work=self.work).count(), 1)

    def test_read_notifications_also_retract_and_rejoin_never_revives_or_renotifies(self):
        self.recommend('alice'); self.recommend('bob')
        note = Notification.objects.get(); note.read_at = timezone.now(); note.save(update_fields=['read_at'])
        before_events = list(WorkLikeEvent.objects.order_by('pk').values())
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        self.recommend('bob')
        self.assertEqual(Notification.objects.count(), 1)
        note.refresh_from_db(); self.assertIsNotNone(note.retracted_at); self.assertIsNotNone(note.read_at)
        self.assertEqual(list(WorkLikeEvent.objects.order_by('pk').values()), before_events)
        self.assertEqual(self.browser('alice').get('/api/v1/notifications/').json()['total'], 0)
        self.assertEqual(books.recommendations().filter(work=self.work).count(), 2)

    def test_activity_withdraw_only_retracts_its_scope(self):
        self.recommend('alice', True); self.recommend('bob', True)
        public = Notification.objects.get(event__scope_key='public')
        themed = Notification.objects.get(event__scope_key=f'activity:{self.activity.pk}')
        activities.withdraw(self.activity.pk, self.work.pk, self.people['bob'].principal)
        public.refresh_from_db(); themed.refresh_from_db()
        self.assertIsNone(public.retracted_at); self.assertIsNotNone(themed.retracted_at)
        self.assertTrue(books.recommendations().filter(anonymous_client=self.people['bob'].principal).exists())
        self.assertEqual(self.browser('alice').get('/api/v1/notifications/').json()['total'], 1)
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        self.assertEqual(self.browser('alice').get('/api/v1/notifications/').json()['total'], 0)

    def test_other_actors_and_other_message_kinds_are_untouched(self):
        self.recommend('alice'); self.recommend('bob'); self.recommend('carol')
        books.set_relation(self.work, self.people['bob'].principal, 'like', True)
        protected = list(Notification.objects.exclude(event__actor=self.people['bob'], event__kind='co_recommendation').order_by('pk').values())
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        self.assertEqual(list(Notification.objects.exclude(event__actor=self.people['bob'], event__kind='co_recommendation').order_by('pk').values()), protected)
        self.assertEqual(books.likes().count(), 1)

    def test_withdraw_and_message_retraction_roll_back_together(self):
        self.recommend('alice', True); self.recommend('bob', True)
        client = self.browser('bob')
        for prefix in ['/api/v1/public', f'/api/v1/activities/{self.activity.pk}']:
            with patch('booklist.notifications.retract_co_recommendation', side_effect=OperationalError('synthetic')):
                response = self.post(client, f'{prefix}/works/{self.work.pk}/recommend/', {'enabled': False})
            self.assertEqual(response.status_code, 503)
        self.assertTrue(books.recommendations().filter(anonymous_client=self.people['bob'].principal).exists())
        self.assertTrue(activities.recs(self.activity.pk).filter(anonymous_client=self.people['bob'].principal).exists())
        self.assertFalse(Notification.objects.filter(retracted_at__isnull=False).exists())

    def test_polling_retraction_ids_are_private_validated_and_available_after_read(self):
        self.recommend('alice'); self.recommend('bob')
        note = Notification.objects.get()
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        path = f'/api/v1/notifications/unread/?since={note.pk}'
        self.assertEqual(self.browser('alice').get(path).json(), {'unread_count': 0, 'retracted_ids': [note.pk]})
        self.assertEqual(self.browser('carol').get(path).json(), {'unread_count': 0, 'retracted_ids': []})
        self.assertEqual(Client().get(path).status_code, 401)
        for bad in ('0', '-1', 'true', '9223372036854775808', '1.5', ''):
            self.assertEqual(self.browser('alice').get('/api/v1/notifications/unread/', {'since': bad}).status_code, 400)

    def test_migration_backfills_old_withdrawals_and_preserves_active_merged_notices(self):
        self.recommend('alice'); self.recommend('bob')
        stale = Notification.objects.get(); stale.read_at = timezone.now(); stale.save(update_fields=['read_at'])
        PublicRecommendation.objects.filter(work=self.work, anonymous_client=self.people['bob'].principal).update(withdrawn_at=timezone.now())
        secondary = Work.objects.create(original_submitted_title='被合并书目', public_added_at=timezone.now())
        for name in ('alice', 'bob'):
            books.set_relation(secondary, self.people[name].principal, 'recommend', True)
        retained = Notification.objects.exclude(pk=stale.pk).get()
        proposal = merging.plan(self.work.pk, secondary.pk)
        merging.merge(self.admin, self.work.pk, secondary.pk, {}, proposal['version'], '合成合并')
        # Bob's recommendation on the merged work makes both event histories active.
        third = Work.objects.create(original_submitted_title='旧版撤回', public_added_at=timezone.now())
        for name in ('alice', 'carol'):
            books.set_relation(third, self.people[name].principal, 'recommend', True)
        legacy = Notification.objects.filter(event__work=third).get()
        PublicRecommendation.objects.filter(work=third, anonymous_client=self.people['carol'].principal).update(withdrawn_at=timezone.now())
        before_events = list(WorkLikeEvent.objects.order_by('pk').values())
        before_recs = list(PublicRecommendation.objects.order_by('pk').values())
        migration = importlib.import_module('booklist.migrations.0021_notification_retraction')
        migration.retract_inactive_recommendations(apps, SimpleNamespace(connection=connection))
        legacy.refresh_from_db(); retained.refresh_from_db(); stale.refresh_from_db()
        self.assertIsNotNone(legacy.retracted_at); self.assertIsNone(retained.retracted_at); self.assertIsNone(stale.retracted_at)
        self.assertEqual(list(WorkLikeEvent.objects.order_by('pk').values()), before_events)
        self.assertEqual(list(PublicRecommendation.objects.order_by('pk').values()), before_recs)
        migration.retract_inactive_recommendations(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Notification.objects.get(pk=legacy.pk).retracted_at, legacy.retracted_at)

    def test_withdraw_after_merge_retracts_original_event_work(self):
        secondary = Work.objects.create(original_submitted_title='通知原条目', public_added_at=timezone.now())
        for name in ('alice', 'bob'):
            books.set_relation(secondary, self.people[name].principal, 'recommend', True)
        note = Notification.objects.get()
        self.recommend('alice')
        proposal = merging.plan(self.work.pk, secondary.pk)
        merging.merge(self.admin, self.work.pk, secondary.pk, {}, proposal['version'], '合成合并')
        books.set_relation(self.work, self.people['bob'].principal, 'recommend', False)
        note.refresh_from_db(); self.assertIsNotNone(note.retracted_at)
        self.assertEqual(note.event.work_id, secondary.pk)

    def test_cursor_load_more_does_not_skip_after_visible_messages_retract(self):
        for number in range(55):
            work = Work.objects.create(original_submitted_title=f'分页作品{number}', public_added_at=timezone.now())
            event = WorkLikeEvent.objects.create(work=work, actor=self.people['bob'], scope_key='public')
            Notification.objects.create(event=event, recipient=self.people['alice'], work_title=work.display_title)
        client = self.browser('alice')
        first = client.get('/api/v1/notifications/').json()
        ids = [row['id'] for row in first['results']]
        self.assertEqual(len(ids), 50); self.assertTrue(first['has_more'])
        removed = ids[:2]
        Notification.objects.filter(pk__in=removed).update(retracted_at=timezone.now())
        more = client.get('/api/v1/notifications/', {'before': ids[-1]}).json()
        self.assertEqual(len(more['results']), 5); self.assertFalse(more['has_more'])
        remaining = set(ids) - set(removed) | {row['id'] for row in more['results']}
        self.assertEqual(len(remaining), 53)
        self.assertEqual(remaining, set(Notification.objects.filter(retracted_at__isnull=True).values_list('pk', flat=True)))
        for query in ({'before': '0'}, {'before': 'true'}, {'before': '9223372036854775808'}, {'before': '1', 'page': '1'}):
            self.assertEqual(client.get('/api/v1/notifications/', query).status_code, 400)

    def test_backfill_retracts_read_and_unread_activity_notices_without_touching_public(self):
        self.recommend('alice', True); self.recommend('bob', True); self.recommend('carol', True)
        themed = Notification.objects.filter(event__actor=self.people['carol'], event__scope_key=f'activity:{self.activity.pk}')
        one = themed.first(); one.read_at = timezone.now(); one.save(update_fields=['read_at'])
        activities.recs(self.activity.pk).filter(anonymous_client=self.people['carol'].principal).update(withdrawn_at=timezone.now())
        public_before = list(Notification.objects.filter(event__scope_key='public').order_by('pk').values())
        migration = importlib.import_module('booklist.migrations.0021_notification_retraction')
        migration.retract_inactive_recommendations(apps, SimpleNamespace(connection=connection))
        self.assertEqual(themed.filter(retracted_at__isnull=False).count(), 2)
        self.assertEqual(list(Notification.objects.filter(event__scope_key='public').order_by('pk').values()), public_before)
        one.refresh_from_db(); self.assertIsNotNone(one.read_at)
