from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from booklist import services
from booklist.list_pagination import navigation
from booklist.models import Work, PublicRecommendation, Activity, ActivityWork, Recommendation, Tag, WorkTag


class ShelfPaginationTests(TestCase):
    def setUp(self):
        actor = services.initialize_admin('owner', 'Pagination!Testing-Password-49')
        _, self.person = services.issue_anonymous()
        now = timezone.now()
        self.period = Activity.objects.create(title='分页活动', start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
            scheduled_end_at=now+timedelta(hours=1), status='active', created_by=actor)
        self.tag = Tag.objects.create(name='奇幻', normalized_name='奇幻')
        for number in range(21):
            work = Work.objects.create(original_submitted_title=f'作品{number:02d}', public_added_at=now)
            PublicRecommendation.objects.create(work=work, anonymous_client=self.person)
            WorkTag.objects.create(work=work, tag=self.tag, source_type='source')
            snap = ActivityWork.objects.create(work=work, activity=self.period, snapshot_title=work.display_title)
            Recommendation.objects.create(work=work, activity=self.period, activity_work=snap,
                scope='activity', anonymous_client=self.person)

    def paths(self): return ['/', f'/activities/{self.period.pk}/']

    def test_twenty_per_page_and_numbered_navigation_for_both_shelves(self):
        for path in self.paths():
            first = self.client.get(path)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(len(first.context['results']), 20)
            self.assertEqual(first.context['paging']['count'], 2)
            self.assertContains(first, '第 1 / 2 页')
            self.assertContains(first, 'aria-current="page">1</span>')
            self.assertContains(first, 'aria-label="第 2 页"')
            second = self.client.get(path, {'page': 2})
            self.assertEqual(len(second.context['results']), 1)
            self.assertContains(second, '第 2 / 2 页')
            self.assertFalse(set(r['id'] for r in first.context['results']) & set(r['id'] for r in second.context['results']))
            self.assertIsNone(second.context['paging']['next'])

    def test_public_filter_and_fixed_page_size_survive_all_navigation(self):
        response = self.client.get('/', {'tag': self.tag.pk, 'page_size': 2, 'page': 2})
        self.assertEqual(response.context['total'], 21)
        self.assertContains(response, '<input type="hidden" name="page_size" value="20">', html=True)
        self.assertEqual(response.context['selected_tags'], [self.tag.pk])
        for item in response.context['paging']['links']:
            if not item.get('gap'):
                query = parse_qs(urlsplit(item['url']).query)
                self.assertEqual(query['tag'], [str(self.tag.pk)])
                self.assertEqual(query['page_size'], ['20'])
        for direction in ('previous',):
            self.assertEqual(parse_qs(urlsplit(response.context['paging'][direction]).query)['tag'], [str(self.tag.pk)])

    def test_single_page_still_shows_current_and_total_pages(self):
        PublicRecommendation.objects.filter(work__original_submitted_title='作品20').delete()
        Recommendation.objects.filter(work__original_submitted_title='作品20').delete()
        for path in self.paths():
            response = self.client.get(path, {'page_size': 40})
            self.assertContains(response, '第 1 / 1 页')
            self.assertContains(response, 'aria-current="page">1</span>')
            self.assertContains(response, 'aria-disabled="true">下一页</span>')

    def test_page_size_selector_removed_and_old_urls_use_twenty(self):
        for path in self.paths():
            response = self.client.get(path, {'page_size': 60})
            self.assertEqual(response.context['page_size'], 20)
            self.assertEqual(len(response.context['results']), 20)
            self.assertNotContains(response, 'page-size-select')
            self.assertNotContains(response, 'page-size-form')

    def test_stale_page_after_removals_redirects_to_last_page_and_keeps_filters(self):
        response = self.client.get('/', {'tag': self.tag.pk, 'page': 99, 'page_size': 20})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertEqual(query, {'tag': [str(self.tag.pk)], 'page_size': ['20'], 'sort': ['recommendations'], 'period': ['all'], 'page': ['2']})
        response = self.client.get(f'/activities/{self.period.pk}/', {'page': 99})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response['Location']).query)['page'], ['2'])

    def test_api_contract_keeps_twenty_per_page_and_empty_out_of_range_results(self):
        for path in ('/api/v1/public/works/', f'/api/v1/activities/{self.period.pk}/'):
            result = self.client.get(path).json()
            self.assertEqual((result['page_size'], len(result['results'])), (20, 20))
            self.assertEqual(self.client.get(path, {'page': 99}).json()['results'], [])


class PageWindowTests(SimpleTestCase):
    def test_large_catalog_has_bounded_page_links_and_gaps(self):
        result = navigation({'page': 500, 'page_size': 20, 'total': 20000}, lambda n: f'?page={n}')
        self.assertLessEqual(len(result['links']), 9)
        self.assertEqual([p['number'] for p in result['links'] if not p.get('gap')], [1, 498, 499, 500, 501, 502, 1000])
