from django.test import TestCase
from django.utils import timezone
from booklist import services
from booklist.models import Work, PublicRecommendation


class RandomBookTests(TestCase):
    def setUp(self):
        _, self.person = services.issue_anonymous()

    def work(self, **kwargs):
        row = Work.objects.create(original_submitted_title='随机候选', public_added_at=timezone.now(), **kwargs)
        PublicRecommendation.objects.create(work=row, anonymous_client=self.person)
        return row

    def test_only_current_visible_public_works_are_selected(self):
        chosen = self.work()
        self.work(hidden_at=timezone.now())
        self.work(review_status='pending')
        self.work(merged_into=chosen)
        self.work(source_metadata={'manual_retired_at': '2026-10-23'})
        retired = self.work()
        PublicRecommendation.objects.filter(work=retired).update(withdrawn_at=timezone.now())
        archived = self.work()
        PublicRecommendation.objects.filter(work=archived).update(archived_at=timezone.now())
        invalid = self.work()
        PublicRecommendation.objects.filter(work=invalid).update(invalidated_at=timezone.now())
        Work.objects.create(original_submitted_title='仅在资料库')
        response = self.client.get('/random-book/')
        self.assertRedirects(response, f'/works/{chosen.pk}/', fetch_redirect_response=False)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_empty_shelf_has_a_return_link(self):
        response = self.client.get('/random-book/')
        self.assertContains(response, '公共书单暂无作品')
        self.assertContains(response, '← 返回书单')
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_no_filters_or_writes(self):
        self.work()
        self.assertEqual(self.client.get('/random-book/?q=不存在').status_code, 400)
        self.assertEqual(self.client.post('/random-book/').status_code, 405)
