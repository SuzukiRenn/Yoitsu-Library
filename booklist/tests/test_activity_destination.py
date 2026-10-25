from datetime import timedelta
import importlib
import json
import uuid
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import TestCase, Client, TransactionTestCase
from django.utils import timezone
from booklist import activities, activity_admin, services, public_booklist as books, moderation, catalog
from booklist.activity_export import snapshot as export_snapshot
from booklist.live_stats import snapshot
from booklist.models import Activity, Work, Recommendation, PublicRecommendation, PublicLike, PendingActivitySubmission, ActivityWork, UserWorkMetadata


class ActivityDestinationTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('destination-owner', 'Testing-Destination!Password-49')
        self.token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = self.token
        self.other_token, self.other = services.issue_anonymous()
        self.other_browser = Client(); self.other_browser.cookies[settings.ANONYMOUS_COOKIE] = self.other_token
        now = timezone.now()
        self.period = Activity.objects.create(title='本期主题', start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1), status='active',
            created_by=self.admin, recommendation_destination='activity_only')
        self.work = Work.objects.create(wenku8_book_id=100001, wenku8_title='主题作品', identity_status='matched')

    def data(self, **extra):
        return {'work_id': self.work.pk, 'title': self.work.display_title, 'reason': '本期理由',
                'confirmed_unmatched': False, 'copy_public_reason': True, **extra}

    def update(self, destination=None, **extra):
        data = {'title':self.period.title, 'theme':'', 'start_at':self.period.start_at.isoformat(),
                'end_at':self.period.scheduled_end_at.isoformat(), 'reason':'修改设置', **extra}
        if destination is not None: data['recommendation_destination'] = destination
        return activity_admin.update(self.admin, self.period.pk, data)

    def post(self, path, data, browser=None, key=None):
        return (browser or self.client).post(path, json.dumps(data), content_type='application/json',
                                            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def submit(self, **extra):
        return self.post(f'/api/v1/activities/{self.period.pk}/recommendations/', self.data(**extra))

    def test_activity_only_submission_retry_and_join_never_write_public(self):
        key = str(uuid.uuid4()); url = f'/api/v1/activities/{self.period.pk}/recommendations/'
        for _ in range(2): self.assertEqual(self.post(url,self.data(),key=key).status_code,200)
        for _ in range(2):
            response = self.post(f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}/recommend/',{'enabled':True},self.other_browser)
            self.assertEqual(response.status_code,200,response.content)
            self.assertTrue(response.json()['work']['available'])
        self.assertEqual(activities.recs(self.period.pk).count(),2)
        self.assertFalse(PublicRecommendation.objects.exists())
        self.assertFalse(PublicLike.objects.exists())
        self.assertFalse(books.reasons().exists())
        self.work.refresh_from_db(); self.assertIsNone(self.work.public_added_at)
        self.assertEqual(books.listing()['total'],0)
        self.assertEqual(snapshot([self.work.pk],self.person,self.period.pk)['results'][0]['recommendation_count'],2)
        public = snapshot([self.work.pk],self.person)['results'][0]
        self.assertEqual(public,{'id':self.work.pk,'available':False})

    def test_existing_public_relations_reason_and_independent_submission_are_preserved(self):
        books.submit(self.other,{'work_id':self.work.pk,'title':self.work.display_title,'reason':'原公共理由','confirmed_unmatched':False})
        books.set_relation(self.work,self.other,'like',True)
        before = (list(PublicRecommendation.objects.values()),list(PublicLike.objects.values()),list(books.reasons().values()))
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(before,(list(PublicRecommendation.objects.values()),list(PublicLike.objects.values()),list(books.reasons().values())))
        books.submit(self.person,{'work_id':self.work.pk,'title':self.work.display_title,'reason':'独立公共理由','confirmed_unmatched':False})
        self.assertEqual(books.recommendations().count(),2)
        activities.withdraw(self.period.pk,self.work.pk,self.person)
        self.assertEqual(books.recommendations().count(),2)
        self.assertEqual(books.reasons().count(),2)

    def test_default_public_mode_keeps_unique_public_contribution_and_reason_copy(self):
        self.update('public')
        for _ in range(2): self.assertEqual(self.submit().status_code,200)
        self.assertEqual(books.recommendations().count(),1)
        self.assertEqual(books.reasons().get().reason,'本期理由')
        activities.withdraw(self.period.pk,self.work.pk,self.person)
        self.assertEqual(books.recommendations().count(),1)

    def test_setting_locks_after_first_participation_and_never_unlocks(self):
        self.assertEqual(self.submit().status_code,200)
        self.period.refresh_from_db(); first=self.period.first_recommended_at
        self.assertIsNotNone(first)
        activities.withdraw(self.period.pk,self.work.pk,self.person)
        self.assertFalse(activities.recs(self.period.pk).exists())
        with self.assertRaisesMessage(services.DomainError,'已有参与'): self.update('public')
        self.update(title='仍能修改名称')
        self.period.refresh_from_db();self.assertEqual(self.period.first_recommended_at,first)
        self.assertEqual(self.period.title,'仍能修改名称')

    def test_invalidated_history_and_migration_backfill_still_lock(self):
        self.submit(); rec=activities.recs(self.period.pk).get()
        Recommendation.objects.filter(pk=rec.pk).update(invalidated_at=timezone.now(),archived_at=timezone.now())
        Activity.objects.filter(pk=self.period.pk).update(first_recommended_at=None)
        with self.assertRaises(services.DomainError): self.update('public')
        migration=importlib.import_module('booklist.migrations.0014_activity_recommendation_destination')
        class Editor: pass
        editor=Editor();editor.connection=connection
        migration.preserve_participation_history(apps,editor)
        self.period.refresh_from_db();self.assertEqual(self.period.first_recommended_at,rec.created_at)

    def test_failed_or_stale_submission_does_not_lock_or_create_partial_data(self):
        response=self.submit(work_id=None,title='不会被创建的手工书',expected_recommendation_destination='public')
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['error']['code'],'activity_destination_changed')
        self.assertEqual(Work.objects.count(),1)
        self.period.refresh_from_db();self.assertIsNone(self.period.first_recommended_at)
        self.update('public')
        self.assertEqual(self.submit(expected_recommendation_destination='public').status_code,200)

    def test_manual_creation_metadata_survives_until_all_current_scopes_withdraw(self):
        response=self.submit(work_id=None,title='本期手工新书',metadata={'author':'原作者','description':'简介','tags':['奇幻']})
        self.assertEqual(response.status_code,200,response.content)
        work=Work.objects.get(pk=response.json()['work_id'])
        self.assertIsNone(work.public_added_at)
        self.assertEqual(catalog.work_detail(work.pk)['description'],'简介')
        other_period=Activity.objects.create(title='另一范围',created_by=self.admin,status='active',
            start_at=self.period.start_at,started_at=self.period.started_at,scheduled_end_at=self.period.scheduled_end_at,
            recommendation_destination='activity_only')
        activities.submit(other_period.pk,self.other,{'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False,'copy_public_reason':False})
        activities.withdraw(self.period.pk,work.pk,self.person)
        self.assertTrue(catalog.visible_works().filter(pk=work.pk).exists())
        self.assertTrue(UserWorkMetadata.objects.filter(work=work,archived_at__isnull=True).exists())
        activities.withdraw(other_period.pk,work.pk,self.other)
        self.assertFalse(catalog.visible_works().filter(pk=work.pk).exists())
        self.assertTrue(UserWorkMetadata.objects.filter(work=work,archived_at__isnull=False).exists())

    def test_activity_details_likes_reasons_candidates_random_and_export_work_without_public(self):
        self.submit()
        url=f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}'
        for action,data in [('like',{'enabled':True}),('reason',{'reason':'修改后的本期理由'})]:
            response=self.post(f'{url}/{action}/',data);self.assertEqual(response.status_code,200,response.content)
        rec=activities.recs(self.period.pk).get()
        response=self.post(f'{url}/reasons/{rec.pk}/like/',{'enabled':True},self.other_browser)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['reason']['like_count'],1)
        page=self.client.get(f'/works/{self.work.pk}/?activity={self.period.pk}')
        self.assertContains(page,'修改后的本期理由'); self.assertContains(page,'本期推荐仅计入本活动')
        self.assertContains(self.client.get(f'/activities/{self.period.pk}/'),'本期推荐仅计入本活动')
        public=self.client.get(f'/works/{self.work.pk}/')
        self.assertNotContains(public,'修改后的本期理由');self.assertNotContains(public,'data-action="like"')
        candidate=self.client.get('/api/v1/recommend/candidates/',{'q':self.work.display_title,'activity':self.period.pk}).json()['results'][0]
        self.assertTrue(candidate['recommended']);self.assertEqual(candidate['recommendation_count'],1)
        public_candidate=self.client.get('/api/v1/recommend/candidates/',{'q':self.work.display_title}).json()['results'][0]
        self.assertFalse(public_candidate['recommended']);self.assertEqual(public_candidate['recommendation_count'],0)
        self.assertRedirects(self.client.get('/random-book/',{'activity':self.period.pk}),f'/works/{self.work.pk}/?activity={self.period.pk}')
        export=export_snapshot(self.period.pk);self.assertEqual(export['total'],1);self.assertEqual(export['works'][0]['like_count'],1)
        self.assertFalse(books.recommendations().exists())

    def test_paused_ended_and_hidden_still_enforce_existing_governance(self):
        self.submit();self.period.status='paused';self.period.save()
        self.assertEqual(self.submit().status_code,409)
        self.period.status='active';self.period.save()
        self.work.hidden_at=timezone.now();self.work.save()
        self.assertEqual(self.submit().status_code,404)
        self.work.hidden_at=None;self.work.save()
        now=timezone.now();self.period.ended_at=now
        activities.transition(self.period,'ended',now,now,'manual',self.admin)
        self.assertEqual(self.submit().status_code,409)
        self.assertContains(self.client.get(f'/works/{self.work.pk}/?activity={self.period.pk}'),'本期理由')
        with self.assertRaises(services.DomainError): self.update('public')

    def pending(self, destination='activity_only'):
        self.period.recommendation_destination=destination;self.period.save()
        self.work.hidden_at=timezone.now();self.work.save()
        response=self.submit(work_id=None,title=self.work.display_title,confirmed_unmatched=True)
        self.assertEqual(response.status_code,200,response.content)
        claim=PendingActivitySubmission.objects.get()
        self.assertEqual(response.json()['status'],'pending')
        return claim

    def test_pending_approval_uses_captured_scope_and_locks_only_on_approval(self):
        claim=self.pending()
        self.period.refresh_from_db();self.assertIsNone(self.period.first_recommended_at)
        self.assertFalse(activities.destination_locked(self.period))
        moderation.approve(self.admin,claim.work_id,'核实为独立作品')
        self.period.refresh_from_db();self.assertIsNotNone(self.period.first_recommended_at)
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        self.assertFalse(books.recommendations().exists());self.assertFalse(books.reasons().exists())
        self.assertEqual(snapshot([claim.work_id],self.person,self.period.pk)['results'][0]['recommendation_count'],1)

    def test_pending_public_approval_preserves_old_behavior(self):
        claim=self.pending('public');moderation.approve(self.admin,claim.work_id,'确认')
        self.assertEqual(books.recommendations().count(),1);self.assertEqual(books.reasons().get().reason,'本期理由')

    def test_pending_merge_and_undo_keep_scope_and_permanent_activity_lock(self):
        from booklist import merging
        claim=self.pending()
        primary=Work.objects.create(wenku8_book_id=100002,identity_status='matched',wenku8_title='已确认的正式作品')
        proposal=merging.plan(primary.pk,claim.work_id)
        choices={item['key']:'primary' for item in proposal['conflicts']}
        operation=merging.merge(self.admin,primary.pk,claim.work_id,choices,proposal['version'],'确认同一作品')
        self.assertEqual(activities.recs(self.period.pk).get().work_id,primary.pk)
        self.assertFalse(books.recommendations().exists())
        self.period.refresh_from_db();first=self.period.first_recommended_at
        self.assertIsNotNone(first)
        merging.undo(self.admin,operation.pk,'撤销身份判断')
        self.period.refresh_from_db();self.assertEqual(self.period.first_recommended_at,first)
        self.assertFalse(books.recommendations().exists())
        with self.assertRaises(services.DomainError): self.update('public')

    def test_pending_changed_scope_approval_rolls_back_without_public_leak(self):
        claim=self.pending();self.update('public')
        with self.assertRaisesMessage(services.DomainError,'归属已变更'):
            moderation.approve(self.admin,claim.work_id,'确认')
        claim.work.refresh_from_db();self.assertEqual(claim.work.review_status,'pending')
        self.assertFalse(Recommendation.objects.exists());self.assertFalse(ActivityWork.objects.exists())
        self.assertFalse(books.recommendations().exists())
        self.update('activity_only');moderation.approve(self.admin,claim.work_id,'恢复原归属后确认')
        self.assertFalse(books.recommendations().exists())

    def test_admin_api_permissions_validation_default_and_locked_field(self):
        path='/api/v1/admin/activities/'
        data={'title':'新活动','theme':'','start_at':(timezone.now()+timedelta(days=3)).isoformat(),
            'end_at':(timezone.now()+timedelta(days=4)).isoformat()}
        self.assertEqual(self.post(path,data).status_code,401)
        token,_=services.login('destination-owner','Testing-Destination!Password-49')
        self.client.cookies[settings.ADMIN_COOKIE]=token
        default=self.post(path,data);self.assertEqual(default.status_code,201)
        self.assertEqual(default.json()['recommendation_destination'],'public')
        chosen=self.post(path,{**data,'recommendation_destination':'activity_only'})
        self.assertEqual(chosen.status_code,201)
        self.assertEqual(chosen.json()['recommendation_destination'],'activity_only')
        self.assertEqual(self.post(path,{**data,'recommendation_destination':'other'}).status_code,400)
        self.assertEqual(self.post(path,{**data,'recommendation_destination':True}).status_code,400)
        page=self.client.get(f'/manage/activities/{self.period.pk}/')
        self.assertContains(page,'select name="recommendation_destination"')
        self.submit()
        page=self.client.get(f'/manage/activities/{self.period.pk}/')
        self.assertContains(page,'已有参与，不可修改');self.assertNotContains(page,'select name="recommendation_destination"')

    def test_form_hides_public_reason_toggle_for_activity_only(self):
        page=self.client.get(f'/activities/{self.period.pk}/recommend/')
        self.assertContains(page,'本期推荐仅计入本活动');self.assertContains(page,'推荐到本期')
        self.assertNotContains(page,'id="copy-public-reason"')
        self.update('public')
        self.assertContains(self.client.get(f'/activities/{self.period.pk}/recommend/'),'id="copy-public-reason"')


from booklist.tests import test_activities as activity_tests


class DestinationConcurrentTests(TransactionTestCase):
    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            triggers=cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name,_ in triggers: cursor.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        try: super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _,sql in triggers: cursor.execute(sql)
    concurrent = activity_tests.MonthlyConcurrentTests.concurrent
    def fixture(self):
        admin=services.initialize_admin('race-owner','Race-Destination!Testing-2026')
        now=timezone.now()
        period=Activity.objects.create(title='并发归属',status='active',start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(hours=1),created_by=admin)
        work=Work.objects.create(wenku8_book_id=200001,identity_status='matched',wenku8_title='并发作品')
        person=services.issue_anonymous()[1]
        update={'title':period.title,'theme':'','start_at':period.start_at.isoformat(),
                'end_at':period.scheduled_end_at.isoformat(),'reason':'调整归属','recommendation_destination':'activity_only'}
        data={'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False,'copy_public_reason':False}
        return admin,period,work,person,update,data

    def test_mode_change_and_first_submission_serialize_without_mixed_scope(self):
        admin,period,work,person,update,data=self.fixture()
        def operate(action):
            if action=='update': return activity_admin.update(admin,period.pk,update)['recommendation_destination']
            return activities.submit(period.pk,person,data)['status']
        outcomes=self.concurrent(['update','submit'],operate)
        period.refresh_from_db()
        self.assertEqual(Recommendation.objects.count(),1)
        self.assertIsNotNone(period.first_recommended_at)
        if period.recommendation_destination=='public':
            self.assertEqual(outcomes,['activity_destination_locked','saved'])
            self.assertEqual(PublicRecommendation.objects.count(),1)
        else:
            self.assertEqual(outcomes,['activity_only','saved'])
            self.assertFalse(PublicRecommendation.objects.exists())

    def test_stale_form_and_mode_change_cannot_commit_wrong_destination(self):
        admin,period,work,person,update,data=self.fixture()
        data['expected_recommendation_destination']='public'
        def operate(action):
            if action=='update': return activity_admin.update(admin,period.pk,update)['recommendation_destination']
            return activities.submit(period.pk,person,data)['status']
        outcomes=self.concurrent(['update','submit'],operate)
        period.refresh_from_db()
        if period.recommendation_destination=='public':
            self.assertEqual(outcomes,['activity_destination_locked','saved'])
            self.assertEqual(PublicRecommendation.objects.count(),1)
        else:
            self.assertEqual(outcomes,['activity_only','activity_destination_changed'])
            self.assertFalse(Recommendation.objects.exists());self.assertFalse(PublicRecommendation.objects.exists())
            self.assertIsNone(period.first_recommended_at)
