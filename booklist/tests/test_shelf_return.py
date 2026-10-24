from urllib.parse import urlencode
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from booklist import services, activities, public_booklist as books
from booklist.catalog_views import shelf_return_url
from booklist.models import Work,Activity


class ShelfReturnTests(TestCase):
    def test_source_validation_keeps_only_matching_shelf_and_valid_filters(self):
        public='/?page=3&tag=12&tag=11&q=%E6%81%8B%E7%88%B1&sort=likes&period=month&page_size=20'
        self.assertEqual(shelf_return_url(public),public)
        activity='/activities/3/?page=2&tag=11&q=book&sort=newest'
        self.assertEqual(shelf_return_url(activity,'3'),activity)
        for bad in ['https://evil.test/', '//evil.test/', '/\\evil.test/', '/works/1/', '/?page=0', '/?page=2&page=3', '/?sort=invalid', '/?unexpected=1', '/?tag=x', '/?q='+('x'*101), '/#anchor']:
            self.assertEqual(shelf_return_url(bad),'/')
        self.assertEqual(shelf_return_url('/?q=test','3'),'/activities/3/')
        self.assertEqual(shelf_return_url('/activities/4/?page=2','3'),'/activities/3/')
        self.assertEqual(shelf_return_url('/activities/3/?period=week','3'),'/activities/3/')

    def test_work_return_link_uses_explicit_source_and_safe_fallback(self):
        _,actor=services.issue_anonymous()
        work=Work.objects.create(original_submitted_title='返回测试')
        books.set_relation(work,actor,'recommend',True)
        source='/?page=3&tag=11&q=test&sort=likes'
        page=self.client.get(f'/works/{work.pk}/?'+urlencode({'from':source}))
        self.assertContains(page,'href="/?page=3&amp;tag=11&amp;q=test&amp;sort=likes"')
        self.assertContains(self.client.get(f'/works/{work.pk}/'),'data-shelf-return href="/"')
        self.assertContains(self.client.get(f'/works/{work.pk}/?from=https://evil.test/'),'data-shelf-return href="/"')
        admin=services.initialize_admin('returnowner','Shelf-Return!Testing-Password-49')
        now=timezone.now()
        period=Activity.objects.create(title='返回活动',status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1),created_by=admin)
        activities.submit(period.pk,actor,{'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False,'copy_public_reason':False})
        source=f'/activities/{period.pk}/?page=2&sort=likes'
        page=self.client.get(f'/works/{work.pk}/?'+urlencode({'activity':period.pk,'from':source}))
        self.assertContains(page,f'data-shelf-return href="/activities/{period.pk}/?page=2&amp;sort=likes"')
        self.assertContains(self.client.get(f'/works/{work.pk}/?activity={period.pk}'),f'data-shelf-return href="/activities/{period.pk}/"')
