from datetime import timedelta
import json
import uuid
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone
from booklist import activities, services, public_booklist as books
from booklist.live_stats import snapshot
from booklist.models import Work, Recommendation, ReasonLike, PublicLike, PublicRecommendation, Activity


class ReasonLikeTests(TestCase):
    def setUp(self):
        self.token,self.actor = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE]=self.token
        self.work=Work.objects.create(original_submitted_title='理由点赞作品', public_added_at=timezone.now())
        books.set_relation(self.work,self.actor,'recommend',True)
        books.set_reason(self.work,self.actor,'第一条理由')
        self.first=books.reasons().get(work=self.work)
        _,self.other=services.issue_anonymous()
        books.set_relation(self.work,self.other,'recommend',True)
        books.set_reason(self.work,self.other,'第二条理由')
        self.second=books.reasons().get(work=self.work,anonymous_client=self.other)
        admin=services.initialize_admin('reasonowner','Reason-Likes!Testing-Password-49')
        now=timezone.now()
        self.activity=Activity.objects.create(title='理由活动',status='active',start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1),created_by=admin)
        activities.submit(self.activity.pk,self.actor,{'work_id':self.work.pk,'title':self.work.display_title,
            'reason':'活动理由','confirmed_unmatched':False,'copy_public_reason':False})
        self.activity_reason=activities.recs(self.activity.pk).get()

    def url(self,reason=None,activity=None,work=None):
        prefix=f'/api/v1/activities/{activity}' if activity else '/api/v1/public'
        return f'{prefix}/works/{work or self.work.pk}/reasons/{reason or self.second.pk}/like/'

    def post(self,url=None,enabled=True,key=None,browser=None):
        return (browser or self.client).post(url or self.url(),json.dumps({'enabled':enabled}),
            content_type='application/json',HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def test_like_is_idempotent_cancellable_and_does_not_like_the_work(self):
        key=str(uuid.uuid4())
        first=self.post(key=key);second=self.post(key=key)
        self.assertEqual(first.status_code,200);self.assertEqual(first.json(),second.json())
        self.assertEqual(self.post().json()['reason']['like_count'],1)
        self.assertEqual(ReasonLike.objects.count(),1)
        self.assertTrue(first.json()['reason']['liked'])
        self.assertEqual((PublicLike.objects.count(),books.recommendations().count()),(0,2))
        self.assertEqual(self.post(enabled=False).json()['reason']['like_count'],0)
        self.assertFalse(self.post(enabled=False).json()['reason']['liked'])
        self.assertEqual(self.post().json()['reason']['like_count'],1)

    def test_card_and_live_stats_choose_the_same_highest_liked_reason(self):
        self.assertEqual(books.serialize([self.work],self.actor)[0]['top_reason']['id'],self.first.pk)
        self.post()
        row=books.serialize([self.work],self.actor)[0]
        live=snapshot([self.work.pk],self.actor)['results'][0]
        self.assertEqual(row['top_reason']['id'],self.second.pk)
        self.assertEqual(live['reason_preview'],'第二条理由')
        self.assertEqual(live['top_reason'],row['top_reason'])
        self.assertEqual(row['top_reason']['like_count'],1)
        page=self.client.get('/')
        self.assertNotContains(page,'获赞最多')
        self.assertNotContains(page,'data-reason-like')
        self.assertContains(self.client.get(f'/works/{self.work.pk}/'),f'data-reason-id="{self.second.pk}"')
        self.assertEqual(self.post(enabled=False).json()['work']['top_reason']['id'],self.first.pk)

    def test_scope_and_work_ids_cannot_be_crossed(self):
        self.assertEqual(self.post(self.url(self.activity_reason.pk)).status_code,404)
        self.assertEqual(self.post(self.url(self.second.pk,self.activity.pk)).status_code,404)
        work=Work.objects.create(original_submitted_title='另一本书',public_added_at=timezone.now())
        books.set_relation(work,self.actor,'recommend',True)
        self.assertEqual(self.post(self.url(self.second.pk,work=work.pk)).status_code,404)
        response=self.post(self.url(self.activity_reason.pk,self.activity.pk))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['work']['top_reason']['id'],self.activity_reason.pk)
        self.assertEqual(books.serialize([self.work])[0]['top_reason']['like_count'],0)

    def test_hidden_invalid_or_archived_reasons_never_rank_or_accept_likes(self):
        for field in ['text_hidden_at','invalidated_at','archived_at']:
            Recommendation.objects.filter(pk=self.second.pk).update(**{field:timezone.now()})
            self.assertEqual(self.post().status_code,404)
            self.assertEqual(books.serialize([self.work])[0]['top_reason']['id'],self.first.pk)
            Recommendation.objects.filter(pk=self.second.pk).update(**{field:None})
        self.assertEqual(ReasonLike.objects.count(),0)

    def test_empty_and_withdrawn_activity_reasons_cannot_be_liked(self):
        url=self.url(self.activity_reason.pk,self.activity.pk)
        self.activity_reason.reason='';self.activity_reason.save()
        self.assertEqual(self.post(url).status_code,404)
        self.activity_reason.reason='恢复';self.activity_reason.withdrawn_at=timezone.now();self.activity_reason.save()
        self.assertEqual(self.post(url).status_code,404)

    def test_ended_activity_is_readonly_but_preserves_reason_likes(self):
        url=self.url(self.activity_reason.pk,self.activity.pk)
        self.assertEqual(self.post(url).status_code,200)
        self.activity.status='ended';self.activity.ended_at=timezone.now();self.activity.save()
        self.assertEqual(self.post(url,False).status_code,409)
        row=activities.listing(self.activity.pk,self.actor)['results'][0]['top_reason']
        self.assertEqual(row['like_count'],1);self.assertFalse(row['writable'])

    def test_zero_cycle_does_not_revive_reason_likes_and_removal_cascades(self):
        self.post()
        books.set_reason(self.work,self.other,'')
        self.assertEqual(ReasonLike.objects.count(),0)
        self.post(self.url(self.first.pk))
        books.set_relation(self.work,self.actor,'recommend',False)
        books.set_relation(self.work,self.other,'recommend',False)
        self.assertEqual(self.post(self.url(self.first.pk)).status_code,404)
        books.set_relation(self.work,self.actor,'recommend',True)
        books.set_reason(self.work,self.actor,'新一轮')
        row=books.serialize([self.work],self.actor)[0]['top_reason']
        self.assertEqual(row['like_count'],0);self.assertNotEqual(row['id'],self.first.pk)

    def test_stats_pagination_identity_csrf_and_revoked_voters(self):
        self.assertEqual(self.post(browser=Client()).status_code,401)
        strict=Client(enforce_csrf_checks=True);strict.cookies[settings.ANONYMOUS_COOKIE]=self.token
        self.assertEqual(self.post(browser=strict).status_code,403)
        self.post()
        path=f'/api/v1/public/works/{self.work.pk}/reasons/'
        rows=self.client.get(path).json()['results']
        self.assertEqual([(r['reason'],r['like_count']) for r in rows],[('第一条理由',0),('第二条理由',1)])
        stat=self.client.get(path+f'stats/?ids={self.second.pk}').json()['results'][0]
        self.assertTrue(stat['liked']);self.assertEqual(stat['id'],self.second.pk)
        for ids in ['','0','1,abc']:
            self.assertEqual(self.client.get(path+'stats/?ids='+ids).status_code,400)
        self.actor.revoked_at=timezone.now();self.actor.save()
        self.assertEqual(books.serialize([self.work])[0]['top_reason']['like_count'],0)
