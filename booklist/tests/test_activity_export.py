from datetime import timedelta
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone
from booklist import services
from booklist.models import Activity, ActivityWork, Recommendation, Work, ReasonLike, Vote


class ActivityExportTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('owner', 'Synthetic-Admin!Pass-123')
        token, _ = services.login('owner', 'Synthetic-Admin!Pass-123')
        self.client.cookies[settings.ADMIN_COOKIE] = token
        now = timezone.now()
        self.period = Activity.objects.create(title='十月推荐', status='active',
            start_at=now-timedelta(days=1), started_at=now-timedelta(days=1),
            scheduled_end_at=now+timedelta(days=1), created_by=self.admin)
        self.work = Work.objects.create(original_submitted_title='后来的书名')
        self.snap = ActivityWork.objects.create(activity=self.period, work=self.work,
            snapshot_title='快照书名', snapshot_author='快照作者')
        self.api = f'/api/v1/admin/activities/{self.period.pk}/summary/'
        self.preview = f'/manage/activities/{self.period.pk}/summary/'

    def recommend(self, reason='', **extra):
        _, person = services.issue_anonymous()
        return Recommendation.objects.create(scope='activity', activity=self.period,
            activity_work=self.snap, work=self.work, anonymous_client=person, reason=reason, **extra)

    def test_export_is_admin_only_including_direct_api_access(self):
        visitor = Client()
        self.assertEqual(visitor.get(self.api).status_code, 401)
        self.assertRedirects(visitor.get(self.preview), '/manage/login/', fetch_redirect_response=False)
        self.assertNotContains(visitor.get(f'/activities/{self.period.pk}/'), '生成总结图片')
        self.assertContains(self.client.get(f'/activities/{self.period.pk}/'), '生成总结图片')
        services.create_admin(self.admin, 'second-owner', 'Synthetic-Admin!Pass-456')
        self.admin.is_active = False; self.admin.save()
        self.assertEqual(self.client.get(self.api).status_code, 401)

    def test_snapshot_counts_only_visible_reasons_without_exporting_text(self):
        low = self.recommend('普通理由')
        popular = self.recommend('更受赞同的理由')
        self.recommend('隐藏文字', text_hidden_at=timezone.now())
        self.recommend('已撤回', withdrawn_at=timezone.now())
        self.recommend('已失效', invalidated_at=timezone.now())
        self.recommend('')
        ReasonLike.objects.create(recommendation=popular, anonymous_client=low.anonymous_client)
        response = self.client.get(self.api)
        self.assertEqual(response.status_code, 200)
        payload = response.json(); row = payload['works'][0]
        self.assertEqual(row['title'], '快照书名'); self.assertEqual(row['author'], '快照作者')
        self.assertEqual(row['recommendation_count'], 4)
        self.assertEqual(row['reason_count'], 2)
        self.assertNotIn('reasons', row)
        self.assertNotContains(response, '更受赞同的理由')
        self.assertNotContains(response, '隐藏文字')
        self.assertNotIn('anonymous_client', response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn('id', row)
        self.assertEqual(self.client.get(self.api+'?page=2').status_code, 400)

    def test_suppressed_and_zero_recommendation_works_are_not_exported(self):
        self.recommend('可见理由')
        other = Work.objects.create(original_submitted_title='只有点赞')
        snap = ActivityWork.objects.create(activity=self.period, work=other, snapshot_title='只有点赞')
        _, actor = services.issue_anonymous()
        Vote.objects.create(scope='activity', activity=self.period, activity_work=snap, work=other, anonymous_client=actor)
        self.assertEqual(self.client.get(self.api).json()['total'], 1)
        self.work.hidden_at = timezone.now(); self.work.save()
        self.assertEqual(self.client.get(self.api).json()['works'], [])

    def test_export_includes_books_beyond_first_shelf_page_and_ranked_by_recommendations(self):
        for n in range(24):
            work = Work.objects.create(original_submitted_title=f'作品{n}')
            snap = ActivityWork.objects.create(activity=self.period, work=work, snapshot_title=f'作品{n}')
            for _ in range(2 if n == 0 else 1):
                _, actor = services.issue_anonymous()
                Recommendation.objects.create(scope='activity', activity=self.period, activity_work=snap,
                    work=work, anonymous_client=actor, reason='推荐')
        payload = self.client.get(self.api).json()
        self.assertEqual(payload['total'], 24)
        self.assertEqual(payload['works'][0]['title'], '作品0')
        self.assertEqual(payload['recommendations'], 25)

    def test_preview_has_blob_only_image_policy_and_historical_snapshot_is_preserved(self):
        self.recommend('历史理由')
        self.period.status = 'ended'; self.period.ended_at = timezone.now(); self.period.save()
        self.work.original_submitted_title = '新资料'; self.work.save()
        self.assertEqual(self.client.get(self.api).json()['works'][0]['title'], '快照书名')
        response = self.client.get(self.preview)
        self.assertEqual(response.status_code, 200)
        self.assertIn("img-src 'self' blob:", response['Content-Security-Policy'])
        self.assertContains(response, 'activity-export.mjs')

    def test_export_only_uses_cached_snapshot_cover(self):
        from unittest.mock import patch
        self.recommend('封面测试')
        self.snap.snapshot_cover_url = 'https://img.wenku8.com/image/0/123/123s.jpg'
        self.snap.save()
        self.assertIsNone(self.client.get(self.api).json()['works'][0]['cover_url'])
        with patch('booklist.activity_export.cover_cache.local_url', return_value='/media/covers/'+'a'*64+'.jpg') as cached:
            result = self.client.get(self.api).json()['works'][0]
            self.assertEqual(result['cover_url'], '/media/covers/'+'a'*64+'.jpg')
            cached.assert_called_once_with(self.snap.snapshot_cover_url)
