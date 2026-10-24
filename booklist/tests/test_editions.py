from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from booklist import catalog,services,public_booklist as books,activities
from booklist.editions import edition_info
from booklist.models import Work,PublicRecommendation,Activity,ActivityWork,Recommendation


class EditionDisplayTests(TestCase):
    def setUp(self):
        _,self.actor=services.issue_anonymous()
        self.web=Work.objects.create(original_submitted_title='版本验收作品',source_metadata={'edition':'web','publisher_category':'kakuyomu'},public_added_at=timezone.now())
        self.book=Work.objects.create(original_submitted_title='版本验收作品文库',source_metadata={'publisher_category':'GA文库'},public_added_at=timezone.now())
        for work in [self.web,self.book]:PublicRecommendation.objects.create(work=work,anonymous_client=self.actor)

    def test_only_explicit_web_evidence_gets_web_badge(self):
        self.assertEqual(edition_info({'edition':'web','publisher_category':'GA文库'})['edition'],'web')
        self.assertEqual(edition_info({'publisher_category':'GA文库'})['edition_label'],'文库版')
        self.assertEqual(edition_info({'publisher_category':'集英社'})['edition_label'],'集英社')
        self.assertEqual(edition_info({'edition':'book','publisher_category':'集英社'})['edition_label'],'集英社')
        self.assertEqual(edition_info({'edition':'web','publisher_category':'集英社'})['edition_label'],'Web版')
        self.assertNotEqual(edition_info({'publisher_category':'集英社'})['edition'],'web')
        self.assertEqual(edition_info({'edition':'other','publisher_category':'GA文库'})['edition_label'],'其他类型')
        self.assertEqual(edition_info({})['edition'],'unknown')
        self.assertEqual(edition_info({'publisher_category':[]})['edition'],'unknown')
        page=self.client.get('/')
        self.assertContains(page,'class="edition-badge"',count=1)
        self.assertContains(self.client.get('/recommend/?work='+str(self.web.pk)),'class="edition-badge"',count=1)

    def test_detail_identifies_version_without_title_badge(self):
        for work,label in [(self.web,'Web版'),(self.book,'文库版')]:
            page=self.client.get(f'/works/{work.pk}/')
            self.assertContains(page,f'<dt>版本</dt><dd>{label}</dd>',html=True)
            self.assertNotContains(page,'class="edition-badge"')
        candidate=self.client.get('/api/v1/recommend/candidates/?q=版本验收').json()
        self.assertEqual({row['id']:row['edition'] for row in candidate['results']},{self.web.pk:'web',self.book.pk:'book'})

    def test_title_similarity_and_missing_binding_never_imply_web(self):
        work=Work.objects.create(original_submitted_title='Web版文库版未确认',source_metadata={})
        self.assertEqual(catalog.work_detail(work.pk)['edition'],'unknown')
        self.assertNotContains(self.client.get(f'/recommend/?work={work.pk}'),'class="edition-badge"')

    def test_activity_keeps_snapshot_title_and_separate_version_interactions(self):
        admin=services.initialize_admin('editionowner','Edition-Testing!Password-49')
        now=timezone.now()
        period=Activity.objects.create(title='版本主题',created_by=admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        for work in [self.web,self.book]:
            snap=ActivityWork.objects.create(work=work,activity=period,snapshot_title='快照'+work.display_title)
            Recommendation.objects.create(work=work,activity=period,activity_work=snap,scope='activity',anonymous_client=self.actor)
        results=activities.listing(period.pk)['results']
        self.assertEqual({row['id']:row['edition'] for row in results},{self.web.pk:'web',self.book.pk:'book'})
        self.assertTrue(all(row['title'].startswith('快照') for row in results))
        self.assertTrue(all(row['recommendation_count']==1 for row in results))
        page=self.client.get(f'/activities/{period.pk}/')
        self.assertContains(page,'class="edition-badge"',count=1)
        detail=self.client.get(f'/works/{self.web.pk}/?activity={period.pk}')
        self.assertContains(detail,'<dt>版本</dt><dd>Web版</dd>',html=True)
        self.assertNotContains(detail,'class="edition-badge"')
