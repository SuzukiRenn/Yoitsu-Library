import json
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from booklist import activities,activity_admin,moderation,merging,services,public_booklist as books
from booklist.models import Activity,Work,Recommendation,PendingActivitySubmission,PublicRecommendation


class ActivityPolicyTests(TestCase):
    def setUp(self):
        self.admin=services.initialize_admin('policy-admin','Policy-Testing!Strong-Password-932')
        _,self.person=services.issue_anonymous()
        now=timezone.now()
        self.period=Activity.objects.create(title='仅本期',sync_public=False,status='active',created_by=self.admin,
            start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        self.work=Work.objects.create(original_submitted_title='仅本期作品')

    def submit(self,**kwargs):
        return activities.submit(self.period.pk,self.person,{'work_id':self.work.pk,'title':self.work.display_title,
            'reason':'本期理由','confirmed_unmatched':False,'copy_public_reason':True,**kwargs})

    def update(self,sync):
        self.period.refresh_from_db()
        return activity_admin.update(self.admin,self.period.pk,{'title':self.period.title,'theme':'主题',
            'start_at':self.period.start_at.isoformat(),'end_at':self.period.scheduled_end_at.isoformat(),
            'reason':'测试规则','sync_public':sync})

    def test_scope_and_public_reason_stay_independent_and_default_is_compatible(self):
        self.submit()
        self.assertEqual(activities.listing(self.period.pk)['total'],1)
        self.assertEqual(books.listing()['total'],0)
        self.assertFalse(PublicRecommendation.objects.exists())
        self.work.refresh_from_db();self.assertIsNone(self.work.public_added_at)
        self.assertFalse(books.reasons().exists())
        self.period.refresh_from_db();self.assertIsNotNone(self.period.participation_locked_at)
        self.assertTrue(Activity._meta.get_field('sync_public').default)

    def test_existing_public_recommendation_reason_and_like_are_untouched(self):
        books.set_relation(self.work,self.person,'recommend',True)
        books.set_reason(self.work,self.person,'原公共理由')
        books.set_relation(self.work,self.person,'like',True)
        before=list(PublicRecommendation.objects.values())
        self.submit();activities.withdraw(self.period.pk,self.work.pk,self.person)
        self.assertEqual(list(PublicRecommendation.objects.values()),before)
        self.assertEqual(books.reasons().get().reason,'原公共理由')
        self.assertEqual(books.likes().count(),1)

    def test_can_choose_before_participation_but_cannot_unlock_after_withdrawal(self):
        self.update(True);self.update(False)
        self.submit();activities.withdraw(self.period.pk,self.work.pk,self.person)
        with self.assertRaises(services.DomainError) as raised:self.update(True)
        self.assertEqual(raised.exception.code,'activity_policy_locked')
        self.update(False)  # Other edits remain possible after participation.

    def test_failed_submission_does_not_lock_policy(self):
        with patch('booklist.public_booklist.resolve_submission',side_effect=services.DomainError('bad','bad')):
            with self.assertRaises(services.DomainError):self.submit()
        self.period.refresh_from_db();self.assertIsNone(self.period.participation_locked_at)
        self.update(True)

    def test_pending_approval_and_merge_respect_policy(self):
        for merge in (False,True):
            work=Work.objects.create(original_submitted_title='申请'+str(merge),review_status='pending')
            PendingActivitySubmission.objects.create(work=work,activity=self.period,anonymous_client=self.person,
                reason='申请理由',copy_public_reason=True,sync_public=False)
            if merge:
                target=Work.objects.create(original_submitted_title='合并主作品')
                plan=merging.plan(target.pk,work.pk)
                merging.merge(self.admin,target.pk,work.pk,{r['key']:'primary' for r in plan['conflicts']},plan['version'],'核实重复')
            else:moderation.approve(self.admin,work.pk,'核实通过')
        self.assertEqual(activities.recs(self.period.pk).count(),2)
        self.assertFalse(PublicRecommendation.objects.exists());self.assertFalse(books.reasons().exists())

    def test_pending_submission_preserves_destination_until_approval(self):
        # A hidden exact duplicate is stored as pending by the ordinary resolver.
        self.work.hidden_at=timezone.now();self.work.save()
        self.assertEqual(self.submit(work_id=None)['status'],'pending')
        self.assertEqual(PendingActivitySubmission.objects.count(),1)
        self.update(True)
        claim=PendingActivitySubmission.objects.get()
        self.assertFalse(claim.sync_public)
        with self.assertRaises(services.DomainError) as raised:
            moderation.approve(self.admin,claim.work_id,'规则变化后核查')
        self.assertEqual(raised.exception.code,'pending_policy_changed')
        self.assertFalse(PublicRecommendation.objects.exists())

    def test_migration_backfills_archived_and_pending_history_without_public_rewrites(self):
        self.submit();activities.withdraw(self.period.pk,self.work.pk,self.person)
        Activity.objects.filter(pk=self.period.pk).update(participation_locked_at=None)
        migration=import_module('booklist.migrations.0019_activity_public_policy')
        from types import SimpleNamespace
        migration.preserve_policy(apps,SimpleNamespace(connection=connection))
        self.period.refresh_from_db();self.assertIsNotNone(self.period.participation_locked_at)
        self.assertFalse(self.period.sync_public);self.assertFalse(PublicRecommendation.objects.exists())

    def test_quick_join_uses_same_policy_and_manual_work_stays_until_last_scope_ends(self):
        self.submit();_,other=services.issue_anonymous()
        activities.recommend(self.period.pk,self.work.pk,other,True)
        activities.recommend(self.period.pk,self.work.pk,other,True)
        self.assertEqual(activities.recs(self.period.pk).count(),2)
        self.assertFalse(PublicRecommendation.objects.exists())
        activities.withdraw(self.period.pk,self.work.pk,self.person)
        self.work.refresh_from_db();self.assertNotIn('manual_retired_at',self.work.source_metadata)
        activities.withdraw(self.period.pk,self.work.pk,other)
        self.work.refresh_from_db();self.assertIn('manual_retired_at',self.work.source_metadata)

    def test_stale_form_policy_is_rejected_before_any_participation(self):
        with self.assertRaises(services.DomainError) as raised:self.submit(expected_sync_public=True)
        self.assertEqual(raised.exception.code,'activity_policy_changed')
        self.assertFalse(Recommendation.objects.exists())
        self.period.refresh_from_db();self.assertIsNone(self.period.participation_locked_at)

    def test_activity_only_detail_and_reason_likes_do_not_need_public_enrollment(self):
        from django.conf import settings
        from booklist import accounts
        from django.test import override_settings
        token,session=accounts.register('scope_reader','范围读者','Only-Activity!Test-Reader-143')
        self.submit()
        self.client.cookies[settings.ACCOUNT_COOKIE]=token
        self.assertEqual(self.client.get(f'/works/{self.work.pk}/?activity={self.period.pk}').status_code,200)
        reason=activities.recs(self.period.pk).get()
        response=self.client.post(f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}/reasons/{reason.pk}/like/',
            json.dumps({'enabled':True}),content_type='application/json',HTTP_IDEMPOTENCY_KEY='activity-reason-test')
        self.assertEqual(response.status_code,200,response.content)
