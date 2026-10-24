from datetime import timedelta
from urllib.parse import urlencode,parse_qs,urlsplit
import json,re
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from booklist import services,public_booklist as books,activities
from booklist.catalog_views import shelf_return_url
from booklist.models import Work,Tag,WorkTag,TagSuppression,PublicRecommendation,PublicLike,Activity,ActivityWork,Recommendation


class TagModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.token,cls.me=services.issue_anonymous();_,cls.other=services.issue_anonymous()
        cls.admin=services.initialize_admin('tag-mode-owner','Tag-Matching-Testing!Password-49')
        cls.a=Tag.objects.create(name='甲',normalized_name='甲');cls.b=Tag.objects.create(name='乙',normalized_name='乙')
        now=timezone.now()
        cls.activity=Activity.objects.create(title='标签主题',created_by=cls.admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        cls.works=[]
        for i,tags in enumerate(([cls.a,cls.b],[cls.a],[cls.b],[])):
            work=Work.objects.create(original_submitted_title=f'标签作品{i}',wenku8_author='同一作者' if i!=2 else '另一作者',public_added_at=now)
            cls.works.append(work);PublicRecommendation.objects.create(work=work,anonymous_client=cls.other)
            for tag in tags:WorkTag.objects.create(work=work,tag=tag,source_type='user',anonymous_client=cls.other)
            snap=ActivityWork.objects.create(activity=cls.activity,work=work,snapshot_title=work.display_title,snapshot_author=work.wenku8_author,snapshot_tags=[{'id':t.pk,'name':t.name} for t in tags])
            Recommendation.objects.create(scope='activity',activity=cls.activity,activity_work=snap,work=work,anonymous_client=cls.other)
        PublicRecommendation.objects.create(work=cls.works[0],anonymous_client=cls.me)
        PublicLike.objects.create(work=cls.works[0],anonymous_client=cls.me)

    def setUp(self):self.client.cookies[settings.ANONYMOUS_COOKIE]=self.token
    def tags(self,mode=None,**extra):
        return urlencode([('tag',self.a.pk),('tag',self.b.pk)]+([('tag_mode',mode)] if mode else [])+list(extra.items()))
    def ids(self,base,query):
        response=self.client.get(base+'?'+query);self.assertEqual(response.status_code,200)
        return {row['id'] for row in response.json()['results']}

    def test_default_all_and_any_in_both_scopes(self):
        for base in ['/api/v1/public/works/',f'/api/v1/activities/{self.activity.pk}/']:
            self.assertEqual(self.ids(base,self.tags()),{self.works[0].pk})
            self.assertEqual(self.ids(base,self.tags('all')),{self.works[0].pk})
            self.assertEqual(self.ids(base,self.tags('any')),{w.pk for w in self.works[:3]})
        self.assertEqual(set(books.listing(tags=[self.a.pk,self.b.pk],tag_mode='any',ids_only=True)),{w.pk for w in self.works[:3]})

    def test_zero_one_duplicate_and_missing_tags(self):
        for base in ['/api/v1/public/works/',f'/api/v1/activities/{self.activity.pk}/']:
            self.assertEqual(self.ids(base,'tag_mode=all'),self.ids(base,'tag_mode=any'))
            self.assertEqual(self.ids(base,f'tag={self.a.pk}&tag_mode=all'),self.ids(base,f'tag={self.a.pk}&tag_mode=any'))
            self.assertEqual(self.ids(base,f'tag={self.a.pk}&tag={self.a.pk}&tag_mode=any'),{w.pk for w in self.works[:2]})
            self.assertEqual(self.ids(base,f'tag={self.a.pk}&tag=999999&tag_mode=all'),set())
            self.assertEqual(self.ids(base,f'tag={self.a.pk}&tag=999999&tag_mode=any'),{w.pk for w in self.works[:2]})

    def test_search_and_personal_filters_remain_and_conditions(self):
        self.assertEqual(self.ids('/api/v1/public/works/',self.tags('any',q='同一作者')),{w.pk for w in self.works[:2]})
        self.assertEqual(self.ids('/api/v1/public/works/',self.tags('any',recommended='1',liked='1')),{self.works[0].pk})
        self.assertEqual(self.ids(f'/api/v1/activities/{self.activity.pk}/',self.tags('any',q='同一作者')),{w.pk for w in self.works[:2]})
        self.assertEqual(self.ids(f'/api/v1/activities/{self.activity.pk}/',self.tags('any',recommended='1')),set())
        self.assertEqual(self.ids('/api/v1/public/works/',self.tags('any',q='不存在')),set())

    def test_effective_tags_and_hidden_works_remain_excluded(self):
        TagSuppression.objects.create(work=self.works[1],tag=self.a,scope='all',admin=self.admin)
        self.works[2].hidden_at=timezone.now();self.works[2].save()
        self.assertEqual(self.ids('/api/v1/public/works/',self.tags('any')),{self.works[0].pk})
        self.b.is_active=False;self.b.save()
        self.assertEqual(self.ids('/api/v1/public/works/',f'tag={self.b.pk}&tag_mode=any'),set())

    def test_ended_activity_uses_snapshot_tags(self):
        WorkTag.objects.all().update(state='admin_removed')
        self.activity.status='ended';self.activity.ended_at=timezone.now();self.activity.save()
        base=f'/api/v1/activities/{self.activity.pk}/'
        self.assertEqual(self.ids(base,self.tags('all')),{self.works[0].pk})
        self.assertEqual(self.ids(base,self.tags('any')),{w.pk for w in self.works[:3]})
        self.assertEqual(self.ids('/api/v1/public/works/',self.tags('any')),set())

    def test_sort_period_pagination_and_redirect_preserve_mode(self):
        response=self.client.get('/?'+self.tags('any',q='标签',sort='likes'))
        self.assertEqual(response.context['tag_mode'],'any');self.assertContains(response,'任一符合')
        for link in response.context['sort_options']+response.context['period_options']:
            self.assertEqual(parse_qs(urlsplit(link['url']).query)['tag_mode'],['any'])
        for base in ['/',f'/activities/{self.activity.pk}/']:
            response=self.client.get(base+'?'+self.tags('any',page=999,q='标签'))
            self.assertEqual(response.status_code,302)
            params=parse_qs(urlsplit(response['Location']).query)
            self.assertEqual(params['tag_mode'],['any']);self.assertEqual(set(params['tag']),{str(self.a.pk),str(self.b.pk)})
            self.assertEqual(params['page'],['1']);self.assertEqual(params['q'],['标签'])
        payload=self.client.get('/api/v1/public/works/?'+self.tags('any',page_size=1,page=2)).json()
        self.assertEqual(payload['total'],3);self.assertEqual(len(payload['results']),1)

    def test_sequence_and_return_context_preserve_any_mode(self):
        for source,extra in [('/?'+self.tags('any',q='标签'),{}),(f'/activities/{self.activity.pk}/?'+self.tags('any'),{'activity':self.activity.pk})]:
            work=self.works[0]
            response=self.client.get(f'/works/{work.pk}/?'+urlencode({'from':source,**extra}))
            self.assertEqual(response.status_code,200)
            match=re.search(r'<script id="work-sequence-data" type="application/json">(.*?)</script>',response.content.decode(),re.S)
            info=json.loads(match.group(1));self.assertEqual(set(info['ids']),{w.pk for w in self.works[:3]})
            self.assertEqual(info['source'],source)
            for name in ['next','previous']:
                if info[name]:self.assertEqual(parse_qs(urlsplit(info[name]).query)['from'],[source])
        self.assertEqual(shelf_return_url('/?tag_mode=any&tag=1'),'/?tag_mode=any&tag=1')
        self.assertEqual(shelf_return_url('/?tag_mode=wrong'),'/')

    def test_invalid_and_duplicate_mode_rejected(self):
        for base in ['/', '/api/v1/public/works/',f'/activities/{self.activity.pk}/',f'/api/v1/activities/{self.activity.pk}/']:
            for suffix in ['tag_mode=or','tag_mode=ANY','tag_mode=','tag_mode=all&tag_mode=any']:
                self.assertEqual(self.client.get(base+'?'+suffix).status_code,400)
