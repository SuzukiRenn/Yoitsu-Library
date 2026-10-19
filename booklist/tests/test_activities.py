from datetime import timedelta
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase
from django.db import connection, connections
from django.utils import timezone
from booklist import activities, activity_admin, services, public_booklist as books
from booklist.models import Activity, ActivityWork, ActivityTransition, Recommendation, Vote, Work, PublicLike, PublicRecommendation, PendingActivitySubmission, AdminAuditLog


class ActivityTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('owner', 'Monthly-Booklist!Testing-Password-49')
        self.now = timezone.now().replace(microsecond=0)
        self.period = Activity.objects.create(title='十月推荐', theme='奇幻', start_at=self.now-timedelta(hours=1),
            scheduled_end_at=self.now+timedelta(days=1), started_at=self.now-timedelta(hours=1), status='active', created_by=self.admin)
        self.work = Work.objects.create(original_submitted_title='群友作品', wenku8_author='原作者')
        token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token

    def post(self, path, data, browser=None, key=None):
        return (browser or self.client).post(path, json.dumps(data), content_type='application/json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def submit(self, reason='', copy=False, browser=None, key=None, work_id='default', title='群友作品'):
        return self.post(f'/api/v1/activities/{self.period.pk}/recommendations/', {
            'work_id':self.work.pk if work_id=='default' else work_id, 'title':title, 'reason':reason,
            'confirmed_unmatched':False,'copy_public_reason':copy},browser,key)

    def action(self, name, data=None, browser=None):
        return self.post(f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}/{name}/',data or {},browser)

    def other(self):
        browser=Client(); token, person=services.issue_anonymous();browser.cookies[settings.ANONYMOUS_COOKIE]=token
        return browser,person

    def test_activity_submission_enrolls_without_public_recommendation_or_likes(self):
        self.assertEqual(self.submit().status_code,200)
        self.work.refresh_from_db();self.assertIsNotNone(self.work.public_added_at)
        self.assertEqual((PublicRecommendation.objects.count(),PublicLike.objects.count(),Vote.objects.count()),(0,0,0))
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        self.assertEqual(activities.listing(self.period.pk)['results'][0]['recommendation_count'],1)
        self.assertEqual(books.listing()['results'][0]['recommendation_count'],0)

    def test_scope_statistics_and_cancellation_do_not_cross(self):
        self.submit('活动理由')
        other,_=self.other();self.submit(browser=other)
        self.action('like',{'enabled':True})
        self.post('/api/v1/public/recommendations/',{'work_id':self.work.pk,'title':'群友作品','reason':'公共理由','confirmed_unmatched':False})
        self.post(f'/api/v1/public/works/{self.work.pk}/like/',{'enabled':True})
        monthly=activities.listing(self.period.pk)['results'][0];public=books.listing()['results'][0]
        self.assertEqual((monthly['recommendation_count'],monthly['like_count']),(2,1))
        self.assertEqual((public['recommendation_count'],public['like_count']),(1,1))
        self.action('withdraw')
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        self.assertEqual((books.recommendations().count(),books.likes().count(),activities.votes(self.period.pk).count()),(1,1,1))

    def test_reason_copy_opt_in_nonempty_no_overwrite(self):
        self.submit('仅活动')
        self.assertFalse(books.reasons().exists())
        self.submit('复制',copy=True)
        self.assertEqual(books.reasons().get().reason,'复制')
        updated=books.reasons().get().updated_at
        self.submit('不覆盖',copy=True)
        self.assertEqual((books.reasons().get().reason,books.reasons().get().updated_at),('复制',updated))
        self.post(f'/api/v1/public/works/{self.work.pk}/reason/',{'reason':''})
        self.submit('未再次勾选')
        self.assertFalse(books.reasons().exists())
        self.submit('',copy=True)
        self.assertFalse(books.reasons().exists())
        self.assertEqual(activities.recs(self.period.pk).count(),1)

    def test_snapshot_reused_after_zero_contributions_and_source_changes(self):
        self.submit();snap=ActivityWork.objects.get()
        self.action('withdraw')
        self.assertEqual(activities.listing(self.period.pk)['total'],0)
        self.assertEqual(self.action('like',{'enabled':True}).status_code,409)
        self.work.title_overridden=True;self.work.title_override='新标题';self.work.author_overridden=True;self.work.author_override='新作者';self.work.save()
        self.submit(title='新标题')
        self.assertEqual(ActivityWork.objects.count(),1)
        row=activities.listing(self.period.pk)['results'][0]
        self.assertEqual((row['title'],row['author']),(snap.snapshot_title,'原作者'))

    def test_like_cannot_create_snapshot_or_enter_another_period(self):
        self.work.public_added_at=self.now;self.work.save()
        self.assertEqual(self.action('like',{'enabled':True}).status_code,409)
        self.assertFalse(ActivityWork.objects.exists())
        self.submit();self.action('like',{'enabled':True});self.action('withdraw')
        self.assertEqual(activities.listing(self.period.pk)['total'],1)
        self.action('like',{'enabled':False})
        self.assertEqual(activities.listing(self.period.pk)['total'],0)

    def test_paused_and_expired_all_writes_readonly(self):
        self.submit('理由');self.action('like',{'enabled':True})
        self.period.status='paused';self.period.save()
        for response in (self.submit(),self.action('withdraw'),self.action('like',{'enabled':False}),self.action('reason',{'reason':'修改'})):
            self.assertEqual(response.status_code,409)
        self.period.status='active';self.period.scheduled_end_at=self.now+timedelta(seconds=1);self.period.save()
        with patch('django.utils.timezone.now',return_value=self.now+timedelta(seconds=2)):
            self.assertEqual(self.submit().status_code,409)
            self.assertEqual(self.action('like',{'enabled':False}).status_code,409)
        self.period.refresh_from_db();self.assertEqual(self.period.status,'ended')
        self.assertEqual(self.period.ended_at,self.now+timedelta(seconds=1))

    def test_offline_whole_window_is_directly_ended_and_idempotent(self):
        self.period.status='ended';self.period.ended_at=self.now;self.period.save()
        row=Activity.objects.create(title='错过时段',start_at=self.now-timedelta(days=2),scheduled_end_at=self.now-timedelta(days=1),status='scheduled',created_by=self.admin)
        activities.advance_due();activities.advance_due();row.refresh_from_db()
        event=ActivityTransition.objects.get(activity=row)
        self.assertEqual((row.status,row.started_at,row.ended_at),('ended',row.start_at,row.scheduled_end_at))
        self.assertEqual((event.from_status,event.to_status,event.effective_at),('scheduled','ended',row.scheduled_end_at))
        self.assertGreater(event.processed_at,event.effective_at)

    def test_auto_adjacent_end_then_start_and_pause_does_not_extend(self):
        edge=self.now+timedelta(seconds=1)
        self.period.status='paused';self.period.scheduled_end_at=edge;self.period.save()
        next_period=Activity.objects.create(title='下一期',start_at=edge,scheduled_end_at=edge+timedelta(days=1),status='scheduled',created_by=self.admin)
        with patch('django.utils.timezone.now',return_value=edge):activities.advance_due()
        self.period.refresh_from_db();next_period.refresh_from_db()
        self.assertEqual((self.period.status,next_period.status),('ended','active'))
        self.assertEqual(next_period.started_at,edge)

    def test_admin_time_conflict_pause_resume_end_cancel_terminal_and_audit(self):
        data={'title':'下一期','theme':'','start_at':(self.now+timedelta(hours=1)).isoformat(),'end_at':(self.now+timedelta(days=2)).isoformat()}
        new=activity_admin.create(self.admin,data)
        with self.assertRaises(services.DomainError):activity_admin.action(self.admin,new['id'],'schedule','安排')
        activity_admin.action(self.admin,self.period.pk,'pause','暂停')
        activity_admin.action(self.admin,self.period.pk,'resume','恢复')
        activity_admin.action(self.admin,self.period.pk,'end','提前结束')
        activity_admin.action(self.admin,new['id'],'schedule','安排')
        activity_admin.action(self.admin,new['id'],'cancel','取消')
        with self.assertRaises(services.DomainError):activity_admin.action(self.admin,self.period.pk,'resume','不可重开')
        self.assertTrue(AdminAuditLog.objects.filter(action='activity.end').exists())

    def test_end_time_update_cannot_overlap_next_or_modify_started_time(self):
        next_period=Activity.objects.create(title='下期',start_at=self.period.scheduled_end_at,scheduled_end_at=self.period.scheduled_end_at+timedelta(days=1),status='scheduled',created_by=self.admin)
        data={'title':self.period.title,'theme':'','start_at':self.period.start_at.isoformat(),'end_at':(next_period.start_at+timedelta(hours=1)).isoformat(),'reason':'延期'}
        with self.assertRaises(services.DomainError):activity_admin.update(self.admin,self.period.pk,data)
        data['end_at']=self.period.scheduled_end_at.isoformat();data['start_at']=(self.period.start_at+timedelta(minutes=1)).isoformat()
        with self.assertRaises(services.DomainError):activity_admin.update(self.admin,self.period.pk,data)

    def test_pending_hidden_same_title_has_request_owner_but_no_participation(self):
        Work.objects.create(original_submitted_title='隐藏同名',hidden_at=self.now)
        response=self.submit(work_id=None,title='隐藏同名',reason='待核查',copy=True)
        self.assertEqual(response.json(),{'status':'pending'})
        pending=PendingActivitySubmission.objects.get()
        self.assertEqual((pending.anonymous_client_id,pending.reason,pending.copy_public_reason),(self.person.pk,'待核查',True))
        self.assertFalse(ActivityWork.objects.exists());self.assertFalse(Recommendation.objects.exists());self.assertFalse(PublicRecommendation.objects.exists())

    def test_governance_and_reason_empty_do_not_reduce_activity_recommenders(self):
        self.submit('原文');Recommendation.objects.filter(scope='activity').update(text_hidden_at=self.now)
        self.action('reason',{'reason':'新文'})
        row=activities.listing(self.period.pk)['results'][0]
        self.assertEqual((row['recommendation_count'],row['reasons']),(1,[]))
        self.assertTrue(AdminAuditLog.objects.filter(before_data__reason='原文').exists())
        self.action('withdraw');self.submit('重提')
        self.assertIsNotNone(activities.recs(self.period.pk).get().text_hidden_at)

    def test_candidate_statuses_are_scope_specific_and_page_is_independent(self):
        self.post('/api/v1/public/recommendations/',{'work_id':self.work.pk,'title':'群友作品','reason':'公共','confirmed_unmatched':False})
        public=self.client.get('/api/v1/recommend/candidates/',{'q':'群友作品'}).json()['results'][0]
        monthly=self.client.get('/api/v1/recommend/candidates/',{'q':'群友作品','activity':self.period.pk}).json()['results'][0]
        self.assertEqual((public['recommendation_count'],public['recommended'],monthly['recommendation_count'],monthly['recommended']),(1,True,0,False))
        self.assertNotContains(self.client.get('/'),'id="recommend-form"')
        self.assertContains(self.client.get('/recommend/'),'id="recommend-form"')
        self.assertContains(self.client.get(f'/activities/{self.period.pk}/recommend/'),f'data-activity="{self.period.pk}"')
        self.assertContains(self.client.get('/'),'月度推荐')

    def test_activity_reason_pagination_sorting_and_public_count_not_used(self):
        for index in range(3):
            browser,_=self.other();self.submit(reason=f'理由{index}',browser=browser)
        second=Work.objects.create(original_submitted_title='第二本')
        self.submit(work_id=second.pk,title='第二本')
        self.post(f'/api/v1/activities/{self.period.pk}/works/{second.pk}/like/',{'enabled':True})
        rows=activities.listing(self.period.pk)['results'];self.assertEqual(rows[0]['id'],second.pk)
        self.assertEqual((rows[1]['reason_count'],len(rows[1]['reasons'])),(3,2))
        response=self.client.get(f'/api/v1/activities/{self.period.pk}/works/{self.work.pk}/reasons/?page=2&page_size=2')
        self.assertEqual(response.json()['results'],[{'reason':'理由2'}])

    def test_drafts_not_exposed_readonly_page_and_admin_auth_required(self):
        draft=Activity.objects.create(title='隐藏草稿',start_at=self.now,scheduled_end_at=self.now+timedelta(days=2),created_by=self.admin)
        self.assertNotContains(self.client.get('/activities/'),'隐藏草稿')
        self.assertEqual(self.client.get(f'/activities/{draft.pk}/').status_code,404)
        self.period.status='paused';self.period.save()
        self.assertNotContains(self.client.get(f'/activities/{self.period.pk}/'),'data-action="like"')
        self.assertContains(self.client.get(f'/activities/{self.period.pk}/recommend/'),'本期活动不可提交')
        self.assertEqual(self.post('/api/v1/admin/activities/',{}).status_code,401)

    def test_activity_idempotency_and_csrf_strict_copy_flag(self):
        key=str(uuid.uuid4());self.submit(key=key);self.submit(key=key)
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        self.assertEqual(self.submit(reason='不同',key=key).status_code,409)
        self.assertEqual(self.submit(copy=1).status_code,400)
        browser=Client(enforce_csrf_checks=True)
        self.assertEqual(self.submit(browser=browser).status_code,403)


class MonthlyConcurrentTests(TransactionTestCase):
    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            triggers=cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name,_ in triggers:cursor.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        try:super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _,sql in triggers:cursor.execute(sql)

    def concurrent(self, values, operation):
        barrier=Barrier(2)
        def run(value):
            connections.close_all();barrier.wait(timeout=10)
            try:return operation(value)
            except services.DomainError as exc:return exc.code
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,values))

    def test_two_simultaneous_schedules_cannot_overlap(self):
        admin=services.initialize_admin('owner','Monthly-Concurrency!Testing-63')
        now=timezone.now()
        ids=[Activity.objects.create(title=str(index),start_at=now+timedelta(hours=1),scheduled_end_at=now+timedelta(hours=2),created_by=admin).pk for index in range(2)]
        def arrange(pk):activity_admin.action(admin,pk,'schedule','安排');return 'scheduled'
        self.assertEqual(sorted(self.concurrent(ids,arrange)),['activity_conflict','scheduled'])
        self.assertEqual(Activity.objects.filter(status='scheduled').count(),1)

    def test_two_simultaneous_recommenders_share_one_snapshot(self):
        from booklist.models import AnonymousClient
        admin=services.initialize_admin('owner','Monthly-Concurrency!Testing-63');now=timezone.now()
        period=Activity.objects.create(title='并发活动',status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(hours=1),created_by=admin)
        work=Work.objects.create(original_submitted_title='同一作品')
        ids=[services.issue_anonymous()[1].pk for _ in range(2)]
        data={'work_id':work.pk,'title':'同一作品','reason':'','confirmed_unmatched':False,'copy_public_reason':False}
        def recommend(pk):
            person=AnonymousClient.objects.get(pk=pk)
            return services.idempotent(person,'monthly.concurrent',str(uuid.uuid4()),data,lambda:activities.submit(period.pk,person,data))['status']
        self.assertEqual(self.concurrent(ids,recommend),['saved','saved'])
        self.assertEqual((ActivityWork.objects.count(),Recommendation.objects.count(),PublicRecommendation.objects.count()),(1,2,0))
