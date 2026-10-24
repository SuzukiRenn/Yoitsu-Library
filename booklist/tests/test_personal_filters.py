from datetime import timedelta
from urllib.parse import urlencode,parse_qs,urlsplit
import json,re
from django.conf import settings
from django.test import TestCase,Client
from django.utils import timezone
from booklist import services,public_booklist as books,activities
from booklist.models import Work,PublicRecommendation,PublicLike,Activity,ActivityWork,Recommendation,Vote,Tag,WorkTag


class PersonalFilterTests(TestCase):
    def setUp(self):
        token,self.me=services.issue_anonymous();_,self.other=services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE]=token
        self.works=[]
        for i in range(4):
            work=Work.objects.create(original_submitted_title=f'个人筛选{i}',public_added_at=timezone.now())
            PublicRecommendation.objects.create(work=work,anonymous_client=self.other)
            self.works.append(work)
        for i in [0,1]:PublicRecommendation.objects.create(work=self.works[i],anonymous_client=self.me)
        for i in [1,2]:PublicLike.objects.create(work=self.works[i],anonymous_client=self.me)
        now=timezone.now();admin=services.initialize_admin('filterowner','Personal-Filter!Testing-Password-49')
        self.activity=Activity.objects.create(title='筛选主题',created_by=admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        for work in self.works:
            snap=ActivityWork.objects.create(activity=self.activity,work=work,snapshot_title=work.display_title)
            Recommendation.objects.create(scope='activity',activity=self.activity,activity_work=snap,work=work,anonymous_client=self.other)
            if work==self.works[3]:Recommendation.objects.create(scope='activity',activity=self.activity,activity_work=snap,work=work,anonymous_client=self.me)
            if work==self.works[0]:Vote.objects.create(scope='activity',activity=self.activity,activity_work=snap,work=work,anonymous_client=self.me)

    def ids(self,url,browser=None):
        response=(browser or self.client).get(url)
        self.assertEqual(response.status_code,200)
        return {row['id'] for row in response.json()['results']}

    def test_personal_public_filters_intersect_and_do_not_show_other_people(self):
        base='/api/v1/public/works/'
        self.assertEqual(self.ids(base+'?recommended=1'),{self.works[0].pk,self.works[1].pk})
        self.assertEqual(self.ids(base+'?liked=1'),{self.works[1].pk,self.works[2].pk})
        self.assertEqual(self.ids(base+'?recommended=1&liked=1'),{self.works[1].pk})
        self.assertEqual(self.ids(base+'?liked=1',Client()),set())

    def test_activity_filters_are_independent_from_public(self):
        base=f'/api/v1/activities/{self.activity.pk}/'
        self.assertEqual(self.ids(base+'?recommended=1'),{self.works[3].pk})
        self.assertEqual(self.ids(base+'?liked=1'),{self.works[0].pk})
        self.assertEqual(self.ids(base+'?recommended=1&liked=1'),set())
        self.assertEqual(self.ids(base+'?recommended=1',Client()),set())

    def test_cancelled_hidden_and_retired_records_are_excluded(self):
        PublicLike.objects.filter(work=self.works[1],anonymous_client=self.me).update(cancelled_at=timezone.now())
        Work.objects.filter(pk=self.works[2].pk).update(hidden_at=timezone.now())
        self.assertEqual(self.ids('/api/v1/public/works/?liked=1'),set())
        PublicRecommendation.objects.filter(work=self.works[0],anonymous_client=self.me).update(withdrawn_at=timezone.now())
        self.assertEqual(self.ids('/api/v1/public/works/?recommended=1'),{self.works[1].pk})
        Vote.objects.filter(activity=self.activity,anonymous_client=self.me).update(cancelled_at=timezone.now())
        self.assertEqual(self.ids(f'/api/v1/activities/{self.activity.pk}/?liked=1'),set())

    def test_search_tag_sort_and_paging_preserve_personal_flags(self):
        tag=Tag.objects.create(name='个人标签')
        WorkTag.objects.create(work=self.works[1],tag=tag,source_type='user',anonymous_client=self.me)
        self.assertEqual(self.ids(f'/api/v1/public/works/?recommended=1&liked=1&tag={tag.pk}&q=个人&sort=likes'),{self.works[1].pk})
        response=self.client.get('/?recommended=1&liked=1')
        self.assertEqual(response.context['filter_count'],2)
        for option in response.context['sort_options']+response.context['period_options']:
            params=parse_qs(urlsplit(option['url']).query);self.assertEqual(params['recommended'],['1']);self.assertEqual(params['liked'],['1'])
        self.assertContains(response,'我的记录');self.assertContains(response,'>推荐过</label>');self.assertContains(response,'>点赞过</label>')
        result=books.listing(self.me,page_size=1,recommended_only=True)
        self.assertEqual(result['total'],2)
        self.assertEqual(len(books.listing(self.me,recommended_only=True,ids_only=True)),2)

    def test_navigation_keeps_personal_scope(self):
        source='/?recommended=1&sort=newest'
        response=self.client.get(f'/works/{self.works[1].pk}/?'+urlencode({'from':source}))
        payload=json.loads(re.search(r'<script id="work-sequence-data" type="application/json">(.*?)</script>',response.content.decode(),re.S).group(1))
        self.assertEqual(set(payload['ids']),{self.works[0].pk,self.works[1].pk})
        source=f'/activities/{self.activity.pk}/?liked=1'
        response=self.client.get(f'/works/{self.works[0].pk}/?'+urlencode({'from':source,'activity':self.activity.pk}))
        payload=json.loads(re.search(r'<script id="work-sequence-data" type="application/json">(.*?)</script>',response.content.decode(),re.S).group(1))
        self.assertEqual(payload['ids'],[self.works[0].pk])

    def test_unknown_duplicate_and_invalid_flags_rejected(self):
        for base in ['/', '/api/v1/public/works/',f'/activities/{self.activity.pk}/',f'/api/v1/activities/{self.activity.pk}/']:
            for value in ['liked=0','recommended=true','liked=1&liked=1','client_id=1']:
                self.assertEqual(self.client.get(base+'?'+value).status_code,400)
