from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase
from django.utils import timezone
from booklist import services, activities, public_booklist as books
from booklist.models import (Work, PublicRecommendation, PublicLike, Activity,
    ActivityWork, Recommendation, Vote, Tag, WorkTag)


class ShelfSortTests(TestCase):
    def setUp(self):
        now = timezone.now()
        admin = services.initialize_admin('sortowner', 'Sorting!Testing-Password-49')
        self.activity = Activity.objects.create(title='排序活动', status='active',
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
            scheduled_end_at=now+timedelta(days=1), created_by=admin)
        people = [services.issue_anonymous()[1] for _ in range(5)]
        self.tag = Tag.objects.create(name='排序标签', normalized_name='排序标签')
        self.works = []
        for index, (recommendations, likes) in enumerate([(3, 1), (2, 4), (1, 0)]):
            work = Work.objects.create(original_submitted_title=f'排序{index}',
                public_added_at=now+timedelta(minutes=index))
            self.works.append(work.pk)
            WorkTag.objects.create(work=work, tag=self.tag, source_type='user', anonymous_client=people[0])
            snap = ActivityWork.objects.create(work=work, activity=self.activity, snapshot_title=work.display_title)
            ActivityWork.objects.filter(pk=snap.pk).update(created_at=now+timedelta(minutes=index))
            for person in people[:recommendations]:
                PublicRecommendation.objects.create(work=work, anonymous_client=person)
                Recommendation.objects.create(work=work, anonymous_client=person, scope='activity',
                    activity=self.activity, activity_work=snap)
            for person in people[:likes]:
                PublicLike.objects.create(work=work, anonymous_client=person)
                Vote.objects.create(work=work, anonymous_client=person, scope='activity',
                    activity=self.activity, activity_work=snap)
            # Inactive historical relations must not increase rank.
            PublicLike.objects.create(work=work, anonymous_client=people[4], cancelled_at=now)
            Vote.objects.create(work=work, anonymous_client=people[4], scope='activity',
                activity=self.activity, activity_work=snap, cancelled_at=now)

    def test_each_sort_is_global_before_pagination_and_counts_do_not_multiply(self):
        a, b, c = self.works
        for sort, expected in [('recommendations', [a,b,c]), ('likes', [b,a,c]), ('newest', [c,b,a])]:
            for listing in [lambda **kw: books.listing(**kw),
                            lambda **kw: activities.listing(self.activity.pk, **kw)]:
                result = listing(sort=sort)
                self.assertEqual([row['id'] for row in result['results']], expected)
                self.assertEqual(result['sort'], sort)
                self.assertEqual(result['total'], 3)
                self.assertEqual({r['id']:(r['recommendation_count'],r['like_count']) for r in result['results']},
                                 {a:(3,1),b:(2,4),c:(1,0)})
                self.assertEqual(listing(sort=sort, page=2, page_size=1)['results'][0]['id'], expected[1])

    def test_activity_defaults_to_recommendations_on_service_page_and_api(self):
        result = activities.listing(self.activity.pk)
        self.assertEqual(result['sort'], 'recommendations')
        self.assertEqual([row['id'] for row in result['results']], self.works)
        page = self.client.get(f'/activities/{self.activity.pk}/')
        self.assertEqual([item['label'] for item in page.context['sort_options'] if item['active']], ['推荐最多'])
        result = self.client.get(f'/api/v1/activities/{self.activity.pk}/').json()
        self.assertEqual(result['sort'], 'recommendations')
        self.assertEqual([row['id'] for row in result['results']], self.works)

    def test_sort_controls_reset_page_but_keep_filters_and_size(self):
        for path in ['/', f'/activities/{self.activity.pk}/']:
            params = {'sort':'likes', 'page_size':20}
            if path == '/': params['tag'] = self.tag.pk
            response = self.client.get(path, params)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'aria-label="书单排序"')
            self.assertEqual(response.context['sort'], 'likes')
            options = response.context['sort_options']
            self.assertEqual([item['label'] for item in options if item['active']], ['点赞最多'])
            for item in options:
                query = parse_qs(urlsplit(item['url']).query)
                self.assertNotIn('page', query)
                self.assertEqual(query['page_size'], ['20'])
                if path == '/': self.assertEqual(query['tag'], [str(self.tag.pk)])

    def test_api_and_page_reject_invalid_or_duplicate_sort(self):
        for path in ['/', '/api/v1/public/works/', f'/activities/{self.activity.pk}/',
                     f'/api/v1/activities/{self.activity.pk}/']:
            for query in ['sort=invalid', 'sort=likes&sort=newest']:
                self.assertEqual(self.client.get(path+'?'+query).status_code, 400)
            self.assertEqual(self.client.get(path+'?sort=newest').status_code, 200)

    def test_equal_metrics_have_stable_order_and_live_counts_change_rank(self):
        a,b,c = self.works
        person = services.issue_anonymous()[1]
        work = Work.objects.get(pk=c)
        for _ in range(5):
            person = services.issue_anonymous()[1]
            PublicLike.objects.create(work=work, anonymous_client=person)
        self.assertEqual(books.listing(sort='likes')['results'][0]['id'], c)
        # Exact metric and time ties always fall back to the fixed identifier.
        PublicLike.objects.all().delete()
        PublicRecommendation.objects.exclude(anonymous_client=person).delete()
        for pk in self.works:
            PublicRecommendation.objects.create(work_id=pk, anonymous_client=person)
        Work.objects.filter(pk__in=self.works).update(public_added_at=timezone.now())
        self.assertEqual([r['id'] for r in books.listing(sort='recommendations')['results']], [a,b,c])


    def test_public_rolling_period_uses_contribution_dates_not_work_dates(self):
        from unittest.mock import patch
        now = timezone.now()
        a, b, c = self.works
        # Lifetime A wins recommendations and B wins likes. Move all old actions outside 30 days.
        PublicRecommendation.objects.update(created_at=now-timedelta(days=40))
        PublicLike.objects.update(created_at=now-timedelta(days=40))
        # B has two weekly recommendations; A one monthly recommendation; C one weekly recommendation.
        for pk, days in [(a, 15), (b, 2), (c, 3)]:
            PublicRecommendation.objects.filter(work_id=pk).update(created_at=now-timedelta(days=days))
        PublicLike.objects.filter(work_id=a, cancelled_at__isnull=True).update(created_at=now-timedelta(days=2))
        PublicLike.objects.filter(work_id=b, cancelled_at__isnull=True).update(created_at=now-timedelta(days=15))
        # Future and cancelled actions must not inflate the rolling rank.
        PublicLike.objects.filter(cancelled_at__isnull=False).update(created_at=now-timedelta(days=1))
        person=services.issue_anonymous()[1]
        future=PublicLike.objects.create(work_id=c,anonymous_client=person)
        PublicLike.objects.filter(pk=future.pk).update(created_at=now+timedelta(seconds=1))
        with patch('booklist.public_booklist.timezone.now', return_value=now):
            week=books.listing(period='week')
            self.assertEqual([r['id'] for r in week['results']], [b,c])
            self.assertEqual(week['total'], 2)
            self.assertEqual(books.listing(period='week',page_size=1,page=2)['results'][0]['id'],c)
            self.assertEqual([r['id'] for r in books.listing(sort='likes',period='week')['results']], [a])
            self.assertEqual([r['id'] for r in books.listing(sort='likes',period='month')['results']], [b,a])
            # Displayed counts remain cumulative and the old all-time behaviour remains available.
            self.assertEqual(week['results'][0]['recommendation_count'],2)
            self.assertEqual(books.listing()['total'],3)
            self.assertEqual(books.listing(sort='newest',period='week')['period'],'week')

    def test_period_boundary_and_moderated_rows(self):
        from unittest.mock import patch
        now=timezone.now();a,b,c=self.works
        PublicRecommendation.objects.update(created_at=now-timedelta(days=40))
        for pk, age in [(a,timedelta(days=7)), (b,timedelta(days=7,microseconds=1)), (c,timedelta(days=1))]:
            PublicRecommendation.objects.filter(work_id=pk).update(created_at=now-age)
        PublicRecommendation.objects.filter(work_id=c).update(invalidated_at=now)
        with patch('booklist.public_booklist.timezone.now',return_value=now):
            self.assertEqual([r['id'] for r in books.listing(period='week')['results']],[a])

    def test_public_period_contract_links_and_activity_is_unchanged(self):
        for path in ['/', '/api/v1/public/works/']:
            for query in ['period=invalid','period=week&period=month']:
                self.assertEqual(self.client.get(path+'?'+query).status_code,400)
            self.assertEqual(self.client.get(path+'?period=week').status_code,200)
        page=self.client.get('/',{'period':'month','sort':'likes','tag':self.tag.pk})
        self.assertEqual(page.context['period'],'month')
        for option in page.context['period_options']:
            query=parse_qs(urlsplit(option['url']).query)
            self.assertEqual(query['sort'],['likes']);self.assertNotIn('page',query)
            self.assertEqual(query['tag'],[str(self.tag.pk)])
        for option in page.context['sort_options']:
            query=parse_qs(urlsplit(option['url']).query)
            self.assertEqual(query['period'],['month'])
        for path in [f'/activities/{self.activity.pk}/',f'/api/v1/activities/{self.activity.pk}/']:
            self.assertEqual(self.client.get(path+'?period=week').status_code,400)
        self.assertNotContains(self.client.get(f'/activities/{self.activity.pk}/'),'近一个月')

    def test_newest_period_filters_enrollment_dates_before_pagination(self):
        from unittest.mock import patch
        now=timezone.now();a,b,c=self.works
        # Old enrolled work has fresh recommendations; newest must still exclude it.
        Work.objects.filter(pk=a).update(public_added_at=now-timedelta(days=31))
        Work.objects.filter(pk=b).update(public_added_at=now-timedelta(days=10))
        Work.objects.filter(pk=c).update(public_added_at=now-timedelta(days=7))
        PublicRecommendation.objects.update(created_at=now-timedelta(days=40))
        PublicLike.objects.update(created_at=now-timedelta(days=40))
        with patch('booklist.public_booklist.timezone.now',return_value=now):
            self.assertEqual([r['id'] for r in books.listing(sort='newest',period='week')['results']],[c])
            month=books.listing(sort='newest',period='month',page_size=1)
            self.assertEqual(month['total'],2)
            self.assertEqual(month['results'][0]['id'],c)
            self.assertEqual(books.listing(sort='newest',period='month',page_size=1,page=2)['results'][0]['id'],b)
            self.assertEqual(books.listing(sort='recommendations',period='week')['total'],0)
            Work.objects.filter(pk=c).update(public_added_at=now-timedelta(days=7,microseconds=1))
            self.assertEqual(books.listing(sort='newest',period='week')['total'],0)
            Work.objects.filter(pk=c).update(public_added_at=now+timedelta(seconds=1))
            self.assertEqual(books.listing(sort='newest',period='week')['total'],0)
        page=self.client.get('/',{'sort':'newest','period':'month','q':'排序','tag':self.tag.pk})
        self.assertContains(page,'class="shelf-period"')
        self.assertNotContains(page,'class="shelf-period" hidden')
        for option in page.context['sort_options']:
            params=parse_qs(urlsplit(option['url']).query)
            self.assertEqual(params['period'],['month'])
            self.assertEqual(params['q'],['排序'])
