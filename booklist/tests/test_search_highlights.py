from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from booklist import activities,public_booklist as books,services
from booklist.models import Work,WorkAlias,Activity
from booklist.templatetags.search_highlight import match_parts


class SearchHighlightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=services.initialize_admin('highlight-owner','Testing-Highlights!Password-49')
        cls.person=services.issue_anonymous()[1]

    def work(self,title,author='测试作者'):
        work=Work.objects.create(wenku8_book_id=100000+Work.objects.count(),identity_status='matched',wenku8_title=title,wenku8_author=author)
        books.enroll(work);books.set_relation(work,self.person,'recommend',True)
        return work

    def test_visible_title_author_and_repeated_matches_do_not_change_plain_api_fields(self):
        work=self.work('Alpha alpha','ALPHA作家')
        response=self.client.get('/',{'q':'alpha'})
        self.assertContains(response,'<mark class="search-match">Alpha</mark> <mark class="search-match">alpha</mark>',html=False)
        self.assertContains(response,'<mark class="search-match">ALPHA</mark>作家',html=False)
        row=self.client.get('/api/v1/public/works/',{'q':'alpha'}).json()['results'][0]
        self.assertEqual((row['id'],row['title'],row['author'],row['recommendation_count']),(work.pk,'Alpha alpha','ALPHA作家',1))

    def test_alias_only_match_does_not_invent_visible_matches_or_display_alias(self):
        work=self.work('可见作品')
        WorkAlias.objects.create(work=work,alias='隐藏别名',normalized_alias='隐藏别名')
        response=self.client.get('/',{'q':'隐藏别名'})
        self.assertContains(response,'可见作品')
        self.assertNotContains(response,'<mark class="search-match">')
        self.assertNotContains(response,'匹配别名')
        self.assertEqual(self.client.get('/api/v1/public/works/',{'q':'隐藏别名'}).json()['total'],1)

    def test_html_and_regex_characters_are_literal_and_escaped(self):
        self.work('<img src=x onerror=alert(1)> [a+b]')
        response=self.client.get('/',{'q':'<img'})
        self.assertContains(response,'<mark class="search-match">&lt;img</mark>')
        self.assertNotContains(response,'<img src=x')
        response=self.client.get('/',{'q':'[a+b]'})
        self.assertContains(response,'<mark class="search-match">[a+b]</mark>')

    def test_activity_uses_casefold_and_historical_snapshot_not_current_metadata(self):
        work=self.work('Straße 与旧标题','旧作者')
        now=timezone.now()
        period=Activity.objects.create(title='历史验收',created_by=self.admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        activities.submit(period.pk,self.person,{'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False,'copy_public_reason':False})
        period.status='ended';period.ended_at=now;period.save()
        work.wenku8_title='新标题';work.wenku8_author='新作者';work.save()
        response=self.client.get(f'/activities/{period.pk}/',{'q':'STRASSE'})
        self.assertContains(response,'<mark class="search-match">Straße</mark> 与旧标题')
        self.assertNotContains(response,'新标题')
        response=self.client.get(f'/activities/{period.pk}/',{'q':'旧作者'})
        self.assertContains(response,'<mark class="search-match">旧作者</mark>')

    def test_public_highlighting_preserves_sqlite_ascii_matching(self):
        self.assertEqual(match_parts('Ä Alpha','ä'),[('Ä Alpha',False)])
        self.assertEqual(match_parts('Straße','STRASSE',True),[('Straße',True)])
        self.assertEqual(match_parts('ABC','b'),[('A',False),('B',True),('C',False)])

    def test_pagination_retains_query_and_marks_every_page(self):
        for index in range(21):self.work(f'分页作品{index}')
        first=self.client.get('/',{'q':'分页'})
        self.assertContains(first,'<mark class="search-match">分页</mark>',count=20)
        second=self.client.get('/',{'q':'分页','page':2})
        self.assertContains(second,'<mark class="search-match">分页</mark>',count=1)
        self.assertContains(second,'q=%E5%88%86%E9%A1%B5')
