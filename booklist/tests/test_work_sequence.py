from datetime import timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
import json
import re
from django.test import TestCase
from django.utils import timezone
from booklist import services, public_booklist as books, activities
from booklist.models import Work, Activity, Tag, WorkTag


class WorkSequenceTests(TestCase):
    def setUp(self):
        _, self.actor=services.issue_anonymous()
        self.works=[]
        for index in range(23):
            work=Work.objects.create(original_submitted_title=f'顺序测试{index:02}',wenku8_author='顺序作者')
            books.set_relation(work,self.actor,'recommend',True)
            self.works.append(work)

    def data(self,response):
        found=re.search(r'<script id="work-sequence-data" type="application/json">(.*?)</script>',response.content.decode(),re.S)
        return json.loads(found.group(1)) if found else None

    def detail(self,work,source,**params):
        return self.client.get(f'/works/{work.pk}/?'+urlencode({'from':source,**params}))

    def test_order_crosses_pages_and_keeps_all_source_conditions(self):
        source='/?page=2&page_size=20&q=顺序&sort=newest&period=all'
        expected=books.listing(q='顺序',sort='newest',ids_only=True)
        current=Work.objects.get(pk=expected[19])
        info=self.data(self.detail(current,source))
        self.assertEqual(info['ids'],expected)
        self.assertEqual(info['position'],20)
        self.assertEqual(info['total'],23)
        destination=urlsplit(info['next'])
        self.assertEqual(destination.path,f'/works/{expected[20]}/')
        params=parse_qs(destination.query)
        self.assertEqual(params['from'],[source])
        self.assertEqual(params['browse'],[info['id']])
        following=self.client.get(info['next'])
        self.assertEqual(self.data(following)['id'],info['id'])
        self.assertContains(following,'data-work-sequence')

    def test_first_last_and_only_entry_boundaries(self):
        ids=books.listing(sort='newest',ids_only=True)
        first=self.data(self.detail(Work.objects.get(pk=ids[0]),'/?sort=newest'))
        last=self.data(self.detail(Work.objects.get(pk=ids[-1]),'/?sort=newest'))
        self.assertIsNone(first['previous']);self.assertIsNone(last['next'])
        single=self.data(self.detail(self.works[0],'/?q=顺序测试00'))
        self.assertEqual(single['ids'],[self.works[0].pk]);self.assertIsNone(single['previous']);self.assertIsNone(single['next'])

    def test_direct_random_and_invalid_sources_have_no_sequence(self):
        self.assertIsNone(self.data(self.client.get(f'/works/{self.works[0].pk}/')))
        for source in ['https://evil.test/','/?sort=invalid','/activities/1/','/?unexpected=1']:
            self.assertIsNone(self.data(self.detail(self.works[0],source)))
        self.assertEqual(self.detail(self.works[0],'/',browse='bad').status_code,400)
        self.assertNotIn('from=',self.client.get('/random-book/')['Location'])

    def test_filters_and_eligibility_match_the_shelf(self):
        tag=Tag.objects.create(name='顺序标签')
        for work in self.works[:3]:
            WorkTag.objects.create(work=work,tag=tag,source_type='user',anonymous_client=self.actor,state='active')
        self.works[0].hidden_at=timezone.now();self.works[0].save()
        self.works[1].review_status='pending';self.works[1].save()
        source=f'/?tag={tag.pk}&q=顺序&sort=likes'
        info=self.data(self.detail(self.works[2],source))
        self.assertEqual(info['ids'],[self.works[2].pk])
        for sort in ['newest','likes','recommendations']:
            result=books.listing(sort=sort,page_size=100)
            self.assertEqual(books.listing(sort=sort,ids_only=True),[row['id'] for row in result['results']])

    def test_activity_uses_snapshot_filter_and_separate_scope(self):
        admin=services.initialize_admin('sequence-owner','Sequence-Testing!Password-49')
        now=timezone.now()
        period=Activity.objects.create(title='顺序主题',created_by=admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        for work in self.works[:3]:
            activities.submit(period.pk,self.actor,{'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False,'copy_public_reason':False})
        source=f'/activities/{period.pk}/?page=2&q=顺序作者&sort=newest'
        info=self.data(self.detail(self.works[1],source,activity=period.pk))
        expected=activities.listing(period.pk,q='顺序作者',sort='newest',ids_only=True)
        self.assertEqual(info['ids'],expected)
        self.assertEqual(set(expected),{work.pk for work in self.works[:3]})
        for name in ['previous','next']:
            self.assertEqual(parse_qs(urlsplit(info[name]).query)['activity'],[str(period.pk)])
        self.assertIsNone(self.data(self.detail(self.works[1],'/',activity=period.pk)))
        # Snapshot authors keep historical filters stable even after catalog edits.
        self.works[1].wenku8_author='改名作者';self.works[1].save()
        period.status='ended';period.ended_at=now;period.save()
        self.assertEqual(self.data(self.detail(self.works[1],source,activity=period.pk))['ids'],expected)
