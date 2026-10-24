from datetime import timedelta
import json
import uuid
from unittest.mock import patch
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from booklist import activities, services, moderation, public_booklist as books
from booklist.activity_export import snapshot as export_snapshot
from booklist.live_stats import snapshot
from booklist.models import Activity, PendingActivitySubmission, Recommendation, Work


class SpoilerTests(TestCase):
    def setUp(self):
        token, self.actor = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token
        self.admin = services.initialize_admin('spoilerowner', 'Spoiler-Feature!Testing-Password-49')
        self.work = Work.objects.create(original_submitted_title='剧透测试作品')
        books.set_relation(self.work, self.actor, 'recommend', True)
        now = timezone.now()
        self.period = Activity.objects.create(title='剧透活动', status='active', start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1), created_by=self.admin)

    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

    def payload(self, **extra):
        return {'work_id': self.work.pk, 'title': self.work.display_title, 'reason': '结局细节',
            'confirmed_unmatched': False, 'contains_spoilers': True, **extra}

    def test_public_flag_roundtrip_and_old_client_preserves_it(self):
        response = self.post('/api/v1/public/recommendations/', self.payload())
        self.assertEqual(response.status_code, 200)
        row = books.reasons().get()
        self.assertTrue(row.contains_spoilers)
        url = f'/api/v1/public/works/{self.work.pk}/reason/'
        self.assertEqual(self.post(url, {'reason': '新结局'}).status_code, 200)
        row.refresh_from_db(); self.assertTrue(row.contains_spoilers)
        self.post(url, {'reason': '普通理由', 'contains_spoilers': False})
        row.refresh_from_db(); self.assertFalse(row.contains_spoilers)
        self.assertEqual(self.post(url, {'reason': 'x', 'contains_spoilers': 'true'}).status_code, 400)
        self.post(url, {'reason': '', 'contains_spoilers': True})
        self.assertFalse(books.reasons().exists())

    def test_initial_live_and_paginated_displays_agree(self):
        books.set_reason(self.work, self.actor, '结局细节', True)
        row = books.reasons().get()
        for url in ['/', f'/works/{self.work.pk}/']:
            html = self.client.get(url).content.decode()
            self.assertIn('data-spoiler-reveal', html)
            self.assertIn('data-spoiler-text', html)
            self.assertIn('tabindex="-1" hidden>结局细节', html)
            self.assertNotIn('popover="auto"', html)
            self.assertNotIn('展开剧透', html)
            self.assertContains(self.client.get(url), 'role="tooltip">查看剧透')
        live = snapshot([self.work.pk], self.actor)['results'][0]
        self.assertEqual(live['reason_preview'], '')
        self.assertTrue(live['top_reason']['contains_spoilers'])
        for path in ['?page_size=1', f'stats/?ids={row.pk}']:
            self.assertTrue(self.client.get(f'/api/v1/public/works/{self.work.pk}/reasons/'+path).json()['results'][0]['contains_spoilers'])
        edit = self.client.get(f'/recommend/?work={self.work.pk}&edit=1').content.decode()
        self.assertIn('id="recommend-spoilers" type="checkbox" checked', edit)
        self.assertTrue(self.client.get('/api/v1/recommend/candidates/?q=剧透测试').json()['results'][0]['own_contains_spoilers'])

    def test_activity_copy_history_export_and_empty_reason(self):
        url = f'/api/v1/activities/{self.period.pk}/recommendations/'
        self.assertEqual(self.post(url, self.payload(copy_public_reason=True)).status_code, 200)
        row = activities.recs(self.period.pk).get()
        self.assertTrue(row.contains_spoilers)
        self.assertTrue(books.reasons().get().contains_spoilers)
        self.period.status='ended'; self.period.ended_at=timezone.now(); self.period.save()
        self.assertTrue(activities.listing(self.period.pk)['results'][0]['top_reason']['contains_spoilers'])
        self.assertNotIn('结局细节', json.dumps(export_snapshot(self.period.pk), ensure_ascii=False))
        self.assertEqual(self.post(f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}/reason/', {'reason': '不能编辑', 'contains_spoilers': False}).status_code, 409)
        self.period.status='active'; self.period.ended_at=None; self.period.save()
        activities.reason(self.period.pk, self.work.pk, self.actor, '', True)
        row.refresh_from_db(); self.assertFalse(row.contains_spoilers)
        self.assertFalse(activities.listing(self.period.pk)['results'][0]['reasons'])

    def test_pending_approval_preserves_flag_in_both_scopes(self):
        pending = Work.objects.create(original_submitted_title='待核查作品', review_status='pending')
        with patch('booklist.public_booklist.resolve_submission', return_value=pending):
            activities.submit(self.period.pk, self.actor, self.payload(work_id=pending.pk, title=pending.display_title, copy_public_reason=True))
        self.assertTrue(PendingActivitySubmission.objects.get().contains_spoilers)
        moderation.approve(self.admin, pending.pk, '确认作品')
        self.assertTrue(Recommendation.objects.get(work=pending, scope='activity').contains_spoilers)
        self.assertTrue(books.reasons().get(work=pending).contains_spoilers)

    def test_flag_edit_keeps_reaction_counts_and_owner_boundary(self):
        books.set_reason(self.work, self.actor, '结局细节', True)
        reason=books.reasons().get()
        like=f'/api/v1/public/works/{self.work.pk}/reasons/{reason.pk}/like/'
        response=self.post(like, {'enabled': True})
        self.assertTrue(response.json()['reason']['contains_spoilers'])
        self.assertEqual(response.json()['reason']['like_count'], 1)
        books.set_reason(self.work, self.actor, '结局细节', False)
        self.assertEqual(books.serialize([self.work])[0]['top_reason']['like_count'], 1)
        token, other=services.issue_anonymous(); self.client.cookies[settings.ANONYMOUS_COOKIE]=token
        response=self.post(f'/api/v1/public/works/{self.work.pk}/reason/', {'reason':'越权', 'contains_spoilers':False})
        self.assertEqual(response.status_code, 409)
        reason.refresh_from_db(); self.assertEqual(reason.reason, '结局细节')

    def test_legacy_merge_snapshot_without_flag_can_still_be_undone(self):
        from booklist import merging
        from booklist.models import WorkMergeChange
        secondary=Work.objects.create(original_submitted_title='旧合并作品')
        books.set_reason(secondary,self.actor,'旧普通理由')
        proposal=merging.plan(self.work.pk,secondary.pk)
        operation=merging.merge(self.admin,self.work.pk,secondary.pk,{},proposal['version'],'合并验收')
        changes=list(WorkMergeChange.objects.filter(operation=operation))
        for change in changes:
            change.before_data.pop('contains_spoilers',None)
            change.after_data.pop('contains_spoilers',None)
        # Model historical pre-migration JSON without changing append-only audit rows.
        with patch('booklist.merging.WorkMergeChange.objects.filter',return_value=changes):
            merging.undo(self.admin,operation.pk,'撤销旧记录')
        secondary.refresh_from_db()
        self.assertIsNone(secondary.merged_into_id)
        self.assertFalse(books.reasons().get(work=secondary).contains_spoilers)
