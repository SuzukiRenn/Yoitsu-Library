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
