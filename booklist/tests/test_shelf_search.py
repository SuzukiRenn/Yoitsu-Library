from datetime import timedelta
from urllib.parse import parse_qs,urlsplit
from django.test import TestCase
from django.utils import timezone
from booklist import activities,services,public_booklist as books
from booklist.models import Work,WorkAlias,UserWorkMetadata,PublicRecommendation,Activity,ActivityWork,Recommendation,Tag,WorkTag

class ShelfSearchTests(TestCase):
    def setUp(self):
        now=timezone.now()
        admin=services.initialize_admin('searchowner','Search!Testing-Password-49')
        self.person=services.issue_anonymous()[1]
        self.activity=Activity.objects.create(title='检索活动',status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1),created_by=admin)
        self.tag=Tag.objects.create(name='校园',normalized_name='校园')
        self.a=Work.objects.create(wenku8_book_id=999,identity_status='matched',wenku8_title='旧书名',title_overridden=True,title_override='新书名',wenku8_author='旧作者',author_overridden=True,author_override='Alice',public_added_at=now)
        self.b=Work.objects.create(original_submitted_title='手工作品',public_added_at=now)
        UserWorkMetadata.objects.create(work=self.b,anonymous_client=self.person,author='Alice 手工作者')
        WorkAlias.objects.create(work=self.a,alias='曾用别名',normalized_alias='曾用别名')
        for work in [self.a,self.b]:
            PublicRecommendation.objects.create(work=work,anonymous_client=self.person)
            snap=ActivityWork.objects.create(work=work,activity=self.activity,snapshot_title='快照作品'+str(work.pk),snapshot_author='快照作者',snapshot_tags=[{'id':self.tag.pk,'name':self.tag.name}] if work==self.a else [])
            Recommendation.objects.create(work=work,anonymous_client=self.person,scope='activity',activity=self.activity,activity_work=snap)
        WorkTag.objects.create(work=self.a,tag=self.tag,source_type='user',anonymous_client=self.person)
        Work.objects.create(original_submitted_title='未推荐新书名',wenku8_author='Alice')
    def test_public_search_displayed_titles_aliases_and_manual_authors(self):
        for q,expected in [('新书',[self.a.pk]),('旧书',[]),('旧作者',[]),('alice',[self.a.pk,self.b.pk]),('手工作者',[self.b.pk]),('曾用别名',[self.a.pk])]:
            self.assertEqual(sorted(r['id'] for r in books.listing(q=q)['results']),sorted(expected))
        self.assertEqual(books.listing(q=' alice ',tags=[self.tag.pk])['total'],1)
        self.assertEqual(books.listing(q='alice',page_size=1,page=2)['total'],2)
        self.assertEqual(books.listing(q='none')['total'],0)
    def test_activity_uses_snapshot_title_author_and_tags(self):
        self.assertEqual(activities.listing(self.activity.pk,q='Alice')['total'],0)
        self.assertEqual(activities.listing(self.activity.pk,q='快照作者')['total'],2)
        result=activities.listing(self.activity.pk,q='快照',tags=[self.tag.pk])
        self.assertEqual([r['id'] for r in result['results']],[self.a.pk])
        self.assertEqual(result['tags'],[{'id':self.tag.pk,'name':'校园','count':1}])
        WorkTag.objects.filter(work=self.a).delete()
        self.assertEqual(activities.listing(self.activity.pk,tags=[self.tag.pk])['total'],1)
    def test_hidden_and_withdrawn_works_do_not_leak_through_search(self):
        Work.objects.filter(pk=self.a.pk).update(hidden_at=timezone.now())
        self.assertEqual(books.listing(q='alice')['total'],1)
        self.assertEqual(activities.listing(self.activity.pk,q='快照')['total'],1)
        PublicRecommendation.objects.filter(work=self.b).update(withdrawn_at=timezone.now())
        self.assertEqual(books.listing(q='alice')['total'],0)
    def test_parameters_and_links_keep_search_and_tag_scope(self):
        for path,q in [('/','Alice'),(f'/activities/{self.activity.pk}/','快照')]:
            page=self.client.get(path,{'q':q,'tag':self.tag.pk})
            self.assertEqual(page.status_code,200)
            self.assertContains(page,'data-tag-dropdown')
            self.assertContains(page,'搜索书名或作者')
            self.assertNotContains(page,'展开全部')
            for option in page.context['sort_options']:
                params=parse_qs(urlsplit(option['url']).query)
                self.assertEqual(params['q'],[q]);self.assertEqual(params['tag'],[str(self.tag.pk)])
            for params in [{'q':'a'*101},{'tag':'bad'},{'surprise':'x'}]:
                self.assertEqual(self.client.get(path,params).status_code,400)
            self.assertEqual(self.client.get(path+'?q=a&q=b').status_code,400)
        for path in ['/api/v1/public/works/',f'/api/v1/activities/{self.activity.pk}/']:
            response=self.client.get(path,{'q':'不存在'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['total'],0)
