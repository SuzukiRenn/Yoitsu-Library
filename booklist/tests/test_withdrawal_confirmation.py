from datetime import timedelta
import json
import uuid
from django.conf import settings
from django.test import TestCase, Client
from django.utils import timezone
from booklist import services, public_booklist as books, activities
from booklist.models import Work, Activity, Admin, PublicRecommendation, PublicLike, Recommendation, IdempotencyRecord


class WithdrawalConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = services.initialize_admin('confirmation-owner', 'Testing-confirmation!Password-49')

    def setUp(self):
        token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token
        self.other_browser = Client()
        token, self.other = services.issue_anonymous()
        self.other_browser.cookies[settings.ANONYMOUS_COOKIE] = token
        self.work = Work.objects.create(wenku8_book_id=1000001, identity_status='matched', wenku8_title='来源作品')
        self.enroll(self.person)
        self.url = f'/api/v1/public/works/{self.work.pk}/recommend/'

    def enroll(self, actor):
        return books.submit(actor, {'work_id':self.work.pk, 'title':self.work.display_title,
            'reason':'当前理由', 'confirmed_unmatched':False})

    def post(self, url=None, payload=None, key=None, browser=None):
        return (browser or self.client).post(url or self.url, json.dumps({'enabled':False} if payload is None else payload),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def challenge(self, url=None, payload=None):
        response = self.post(url, payload)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'withdrawal_confirmation_required')
        return response.json()['error']['confirmation']

    def period(self):
        now = timezone.now()
        return Activity.objects.create(title='确认主题', start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
            scheduled_end_at=now+timedelta(days=1), status='active', created_by=self.admin)

    def join(self, period):
        activities.submit(period.pk,self.person,{'work_id':self.work.pk,'title':self.work.display_title,
            'reason':'主题理由','confirmed_unmatched':False,'copy_public_reason':False})

    def test_prompt_and_cancel_do_not_mutate_recommendations_reasons_or_likes(self):
        books.set_relation(self.work,self.person,'like',True)
        before=IdempotencyRecord.objects.count()
        prompt=self.challenge()
        self.assertIn('公共书架',prompt['message'])
        self.assertNotIn('重新填写',prompt['message'])
        self.assertEqual(books.recommendations().filter(work=self.work).count(),1)
        self.assertEqual(books.reasons().filter(work=self.work).count(),1)
        self.assertEqual(books.likes().filter(work=self.work).count(),1)
        self.assertEqual(IdempotencyRecord.objects.count(),before)

    def test_confirmed_retry_replays_even_after_a_new_cycle_starts(self):
        books.set_relation(self.work,self.person,'like',True)
        token=self.challenge()['token'];key=str(uuid.uuid4())
        payload={'enabled':False,'confirmation':token}
        response=self.post(payload=payload,key=key)
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(books.recommendations().filter(work=self.work).exists())
        self.assertFalse(books.likes().filter(work=self.work).exists())
        self.assertTrue(PublicLike.objects.filter(work=self.work,archived_at__isnull=False).exists())
        self.enroll(self.person)
        replay=self.post(payload=payload,key=key)
        self.assertEqual(replay.json(),response.json())
        self.assertEqual(books.recommendations().filter(work=self.work).count(),1)
        stale=self.post(payload=payload)
        self.assertEqual(stale.status_code,409,'Old confirmation must not authorize a new recommendation cycle')

    def test_stale_multiple_count_still_requires_confirmation_at_actual_write(self):
        self.enroll(self.other)
        response=self.post(browser=self.other_browser)
        self.assertEqual(response.status_code,200)
        self.assertTrue(books.reasons().filter(work=self.work).exists())
        self.challenge()
        self.assertTrue(books.recommendations().filter(work=self.work,anonymous_client=self.person).exists())

    def test_new_recommender_after_prompt_prevents_unnecessary_archive(self):
        token=self.challenge()['token']
        self.enroll(self.other)
        response=self.post(payload={'enabled':False,'confirmation':token})
        self.assertEqual(response.status_code,200)
        self.assertEqual(books.recommendations().filter(work=self.work).count(),1)
        self.assertEqual(books.reasons().filter(work=self.work).count(),2)

    def test_tokens_are_bound_to_actor_work_scope_and_expiry(self):
        from unittest.mock import patch
        token=self.challenge()['token']
        self.assertEqual(self.post(payload={'enabled':False,'confirmation':token+'bad'}).status_code,409)
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+301):
            self.assertEqual(self.post(payload={'enabled':False,'confirmation':token}).status_code,409)
        self.enroll(self.other)
        books.set_relation(self.work,self.person,'recommend',False)
        self.assertEqual(self.post(payload={'enabled':False,'confirmation':token},browser=self.other_browser).status_code,409)
        period=self.period();self.join(period)
        url=f'/api/v1/activities/{period.pk}/works/{self.work.pk}/recommend/'
        self.assertEqual(self.post(url,{'enabled':False,'confirmation':token}).status_code,409)

    def test_activity_both_withdraw_routes_keep_public_relations(self):
        period=self.period();self.join(period)
        books.set_relation(self.work,self.person,'like',True)
        activities.like(period.pk,self.work.pk,self.person,True)
        url=f'/api/v1/activities/{period.pk}/works/{self.work.pk}/withdraw/'
        prompt=self.challenge(url,{})
        self.assertIn('公共书架中的推荐不会撤回',prompt['message'])
        response=self.post(url,{'confirmation':prompt['token']})
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(activities.recs(period.pk).filter(work=self.work).exists())
        self.assertTrue(books.recommendations().filter(work=self.work).exists())
        self.assertTrue(books.likes().filter(work=self.work).exists())
        self.assertFalse(activities.votes(period.pk).filter(work=self.work).exists())

    def test_manual_retirement_change_requires_updated_confirmation(self):
        self.work=Work.objects.get(pk=books.submit(self.person,{'work_id':None,'title':'手工作品确认',
            'reason':'理由','confirmed_unmatched':True,'metadata':{'author':'作者','description':'简介','tags':[]}})['work']['id'])
        self.url=f'/api/v1/public/works/{self.work.pk}/recommend/'
        period=self.period();self.join(period)
        first=self.challenge()
        self.assertNotIn('重新填写',first['message'])
        activities.withdraw(period.pk,self.work.pk,self.person)
        changed=self.post(payload={'enabled':False,'confirmation':first['token']})
        self.assertEqual(changed.status_code,409)
        prompt=changed.json()['error']['confirmation']
        self.assertIn('重新填写资料',prompt['message'])
        self.assertEqual(self.post(payload={'enabled':False,'confirmation':prompt['token']}).status_code,200)
        self.work.refresh_from_db();self.assertIn('manual_retired_at',self.work.source_metadata)

    def test_paused_activity_cannot_be_withdrawn_with_previously_valid_token(self):
        period=self.period();self.join(period)
        url=f'/api/v1/activities/{period.pk}/works/{self.work.pk}/recommend/'
        token=self.challenge(url,{'enabled':False})['token']
        period.status='paused';period.save()
        response=self.post(url,{'enabled':False,'confirmation':token})
        self.assertEqual(response.json()['error']['code'],'activity_readonly')
        self.assertTrue(activities.recs(period.pk).filter(work=self.work).exists())


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections
from django.test import TransactionTestCase


class ConcurrentWithdrawalTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Only the disposable test database: append-only triggers intentionally forbid normal flushes.
        from django.db import connection
        with connection.cursor() as cursor:
            triggers=cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name,_ in triggers:
                cursor.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        try:
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _,sql in triggers:
                    cursor.execute(sql)

    def setUp(self):
        self.work=Work.objects.create(wenku8_book_id=2000001,identity_status='matched',wenku8_title='并发撤回')
        self.actors=[services.issue_anonymous() for _ in range(2)]
        for _,actor in self.actors:
            books.submit(actor,{'work_id':self.work.pk,'title':self.work.display_title,'reason':'本轮理由','confirmed_unmatched':False})
        self.url=f'/api/v1/public/works/{self.work.pk}/recommend/'

    def request(self, token, barrier, payload, key):
        connections.close_all()
        browser=Client();browser.cookies[settings.ANONYMOUS_COOKIE]=token
        try:
            barrier.wait(timeout=10)
            response=browser.post(self.url,json.dumps(payload),content_type='application/json',HTTP_IDEMPOTENCY_KEY=key)
            return response.status_code,response.json()
        finally:
            connections.close_all()

    def test_simultaneous_withdrawals_cannot_silently_end_cycle(self):
        barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda pair:self.request(pair[0],barrier,{'enabled':False},str(uuid.uuid4())),self.actors))
        self.assertEqual(sorted(code for code,_ in results),[200,409],results)
        self.assertEqual(books.recommendations().filter(work=self.work).count(),1)
        self.assertEqual(books.reasons().filter(work=self.work).count(),2)

    def test_duplicate_confirmed_requests_share_one_result(self):
        books.set_relation(self.work,self.actors[1][1],'recommend',False)
        browser=Client();browser.cookies[settings.ANONYMOUS_COOKIE]=self.actors[0][0]
        first=browser.post(self.url,json.dumps({'enabled':False}),content_type='application/json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        token=first.json()['error']['confirmation']['token'];key=str(uuid.uuid4());barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.request,self.actors[0][0],barrier,{'enabled':False,'confirmation':token},key) for _ in range(2)]
            results=[future.result() for future in futures]
        self.assertEqual([code for code,_ in results],[200,200],results)
        self.assertEqual(results[0][1],results[1][1])
        self.assertFalse(books.recommendations().filter(work=self.work).exists())
