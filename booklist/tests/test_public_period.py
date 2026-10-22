"""Rolling public rankings use effective event timestamps, not card totals."""
from datetime import datetime, timedelta, timezone as tz
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase
from django.utils import timezone
from booklist import services, activities, public_booklist as books
from booklist.models import (Work, PublicRecommendation, PublicLike, Activity,
    Recommendation, Vote, ActivityWork, Tag, WorkTag)


class PublicPeriodTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = datetime(2026, 10, 21, 8, 12, 34, 567890, tzinfo=tz.utc)
        cls.people = [services.issue_anonymous()[1] for _ in range(12)]
        cls.tag = Tag.objects.create(name='范围样本', normalized_name='范围样本')
        cls.works = []
        # Recent recommendations/likes must outrank larger lifetime totals.
        for index in range(4):
            work = Work.objects.create(original_submitted_title=f'范围作品{index}',
                public_added_at=cls.now-timedelta(days=index+1))
            cls.works.append(work)
            WorkTag.objects.create(work=work, tag=cls.tag, source_type='source')
        a,b,c,d = cls.works
        cls.events(a, PublicRecommendation, [-40]*5 + [-2])
        cls.events(a, PublicLike, [-40]*6 + [-3])
        cls.events(b, PublicRecommendation, [-2]*3)
        cls.events(b, PublicLike, [-1]*2)
        cls.events(c, PublicRecommendation, [-14]*2)
        cls.events(c, PublicLike, [-14]*4)
        cls.events(d, PublicRecommendation, [-40])

    @classmethod
    def events(cls, work, model, days):
        for person, offset in zip(cls.people, days):
            row = model.objects.create(work=work, anonymous_client=person)
            model.objects.filter(pk=row.pk).update(created_at=cls.now+timedelta(days=offset))

    def listing(self, **kwargs):
        with patch('booklist.public_booklist.timezone.now', return_value=self.now):
            return books.listing(**kwargs)

    def get(self, path='/', params=None):
        with patch('booklist.public_booklist.timezone.now', return_value=self.now):
            return self.client.get(path, params or {})

    def test_rolling_rank_differs_from_lifetime_and_cards_keep_total_counts(self):
        a,b,c,d = [work.pk for work in self.works]
        expectations = {
            ('recommendations','all'): [a,b,c,d], ('recommendations','month'): [b,c,a],
            ('recommendations','week'): [b,a], ('likes','all'): [a,c,b,d],
            ('likes','month'): [c,b,a], ('likes','week'): [b,a],
        }
        for (sort, period), ids in expectations.items():
            with self.subTest(sort=sort, period=period):
                result = self.listing(sort=sort, period=period)
                self.assertEqual([row['id'] for row in result['results']], ids)
                self.assertEqual(result['total'], len(ids))
                first = next(row for row in result['results'] if row['id'] == a)
                self.assertEqual((first['recommendation_count'], first['like_count']), (6,7))

    def test_main_metric_must_be_positive_and_secondary_is_same_window(self):
        a,b,_,d = self.works
        # d has an old recommendation, but a recent like can put it on the like list.
        self.events(d, PublicLike, [-1])
        self.assertNotIn(d.pk, [r['id'] for r in self.listing(period='week')['results']])
        self.assertIn(d.pk, [r['id'] for r in self.listing(sort='likes', period='week')['results']])
        # a and b tie on recent recommendations; a's lifetime likes are greater,
        # while b still has more recent likes and must win the tie.
        rows = list(books.recommendations().filter(work=b).order_by('id'))
        PublicRecommendation.objects.filter(pk__in=[row.pk for row in rows[1:]]).update(created_at=self.now-timedelta(days=40))
        self.assertEqual([r['id'] for r in self.listing(period='week')['results']], [b.pk,a.pk])

    def test_window_boundaries_include_cutoff_and_now_but_exclude_future(self):
        for period, days in [('week',7),('month',30)]:
            for model, sort in [(PublicRecommendation,'recommendations'), (PublicLike,'likes')]:
                with self.subTest(period=period, model=model.__name__):
                    work = Work.objects.create(original_submitted_title=f'{period}-{sort}', public_added_at=self.now)
                    # A lifetime recommendation keeps likes eligible for the public shelf.
                    if model is PublicLike:
                        row = PublicRecommendation.objects.create(work=work, anonymous_client=self.people[0])
                        PublicRecommendation.objects.filter(pk=row.pk).update(created_at=self.now-timedelta(days=50))
                    moments = [self.now-timedelta(days=days), self.now,
                        self.now-timedelta(days=days,microseconds=1), self.now+timedelta(microseconds=1)]
                    for person, moment in zip(self.people, moments):
                        row = model.objects.create(work=work, anonymous_client=person)
                        model.objects.filter(pk=row.pk).update(created_at=moment)
                    # Compare to a known one-event entry and inspect ORM ranking,
                    # while serialized cards deliberately retain all effective events.
                    result = self.listing(sort=sort, period=period)
                    row = next(item for item in result['results'] if item['id']==work.pk)
                    self.assertEqual(row['recommendation_count' if model is PublicRecommendation else 'like_count'],4)
                    with patch('booklist.public_booklist.serialize', side_effect=lambda works, client: [
                            {'id': w.pk, 'rank': w.rank_count if sort=='recommendations' else w.rank_likes} for w in works]):
                        ranked = self.listing(sort=sort,period=period)
                    self.assertEqual(next(item['rank'] for item in ranked['results'] if item['id']==work.pk),2)

    def test_inactive_events_never_rank_and_zero_recent_entries_are_excluded(self):
        for field in ('withdrawn_at','invalidated_at','archived_at'):
            work = Work.objects.create(original_submitted_title='无效推荐'+field, public_added_at=self.now)
            old = PublicRecommendation.objects.create(work=work, anonymous_client=self.people[0])
            PublicRecommendation.objects.filter(pk=old.pk).update(created_at=self.now-timedelta(days=40))
            row = PublicRecommendation.objects.create(work=work, anonymous_client=self.people[1])
            PublicRecommendation.objects.filter(pk=row.pk).update(created_at=self.now-timedelta(days=1), **{field:self.now})
            self.assertNotIn(work.pk,[r['id'] for r in self.listing(period='week')['results']])
        for field in ('cancelled_at','invalidated_at','archived_at'):
            work = Work.objects.create(original_submitted_title='无效点赞'+field, public_added_at=self.now)
            self.events(work,PublicRecommendation,[-40])
            row = PublicLike.objects.create(work=work,anonymous_client=self.people[0])
            PublicLike.objects.filter(pk=row.pk).update(created_at=self.now-timedelta(days=1),**{field:self.now})
            self.assertNotIn(work.pk,[r['id'] for r in self.listing(sort='likes',period='week')['results']])

    def test_withdraw_recommend_again_uses_new_record_time(self):
        work = self.works[3]
        # Imported identities survive a zero-recommendation cycle; manual entries
        # instead retire and require the established metadata resubmission flow.
        work.wenku8_book_id=900004
        work.identity_status='matched'
        work.save(update_fields=['wenku8_book_id','identity_status','updated_at'])
        client = self.people[0]
        books.set_relation(work,client,'recommend',False)
        with patch('booklist.public_booklist.timezone.now',return_value=self.now):
            books.set_relation(work,client,'recommend',True)
        self.assertIn(work.pk,[r['id'] for r in self.listing(period='week')['results']])

    def test_filters_global_ranking_before_pagination_and_stable_ties(self):
        result = self.listing(sort='likes',period='month',tags=[self.tag.pk],page=2,page_size=1)
        self.assertEqual(result['results'][0]['id'],self.works[1].pk)
        self.assertEqual(result['total'],3)
        x = Work.objects.create(original_submitted_title='并列甲',public_added_at=self.now)
        y = Work.objects.create(original_submitted_title='并列乙',public_added_at=self.now)
        self.events(x,PublicRecommendation,[-1]*4)
        self.events(y,PublicRecommendation,[-1]*4)
        self.assertEqual([r['id'] for r in self.listing(period='week')['results']][:2],[x.pk,y.pk])

    def test_newest_preserves_period_and_filters_added_time_like_anonymous(self):
        old = self.works[-1]
        Work.objects.filter(pk=old.pk).update(public_added_at=self.now-timedelta(days=40))
        for period in ('all', 'month', 'week'):
            result = self.listing(sort='newest', period=period)
            self.assertEqual(result['period'], period)
            expected = self.works if period == 'all' else self.works[:-1]
            self.assertEqual([row['id'] for row in result['results']], [w.pk for w in expected])
        response = self.get('/', {'sort':'newest', 'period':'week'})
        self.assertContains(response, 'class="shelf-period"')

    def test_html_and_api_share_validation_and_default(self):
        for path in ('/','/api/v1/public/works/'):
            self.assertEqual(self.get(path).status_code,200)
            for query in ('period=bad','period=','period=week&period=week','period=month&period=week',
                          'sort=newest&period=bad'):
                self.assertEqual(self.get(path+'?'+query).status_code,400,path+query)
            self.assertEqual(self.get(path,{'period':'week'}).status_code,200)
        self.assertEqual(self.get('/api/v1/public/works/').json()['period'],'all')

    def test_links_form_and_stale_page_preserve_period_and_tags(self):
        response=self.get('/',{'period':'month','sort':'likes','tag':self.tag.pk})
        self.assertNotContains(response, 'data-period-note')
        self.assertContains(response,'<input type="hidden" name="period" value="month">',html=True)
        for option in response.context['sort_options']:
            params=parse_qs(urlsplit(option['url']).query)
            self.assertEqual(params['period'],['month'])
            self.assertEqual(params['tag'],[str(self.tag.pk)])
            self.assertNotIn('page',params)
        for option in response.context['period_options']:
            params=parse_qs(urlsplit(option['url']).query)
            self.assertEqual(params['sort'],['likes'])
            self.assertEqual(params['tag'],[str(self.tag.pk)])
            self.assertNotIn('page',params)
        response=self.get('/',{'period':'week','sort':'likes','tag':self.tag.pk,'page':99})
        self.assertEqual(response.status_code,302)
        params=parse_qs(urlsplit(response.url).query)
        self.assertEqual(params,{'period':['week'],'sort':['likes'],'tag':[str(self.tag.pk)],'page_size':['20'],'page':['1']})

    def test_short_range_pagination_uses_eligible_count(self):
        for index in range(21):
            work=Work.objects.create(original_submitted_title=f'分页范围{index}',public_added_at=self.now)
            self.events(work,PublicRecommendation,[-1])
        first=self.get('/',{'period':'week'})
        second=self.get('/',{'period':'week','page':2})
        self.assertEqual(first.context['total'],23)
        self.assertEqual(len(first.context['results']),20)
        self.assertEqual(len(second.context['results']),3)
        self.assertFalse({r['id'] for r in first.context['results']} & {r['id'] for r in second.context['results']})
        self.assertEqual(parse_qs(urlsplit(first.context['paging']['next']).query)['period'],['week'])

    def test_activity_refuses_period_and_keeps_lifetime_sort(self):
        admin=services.initialize_admin('periodowner','Period-Activity!Password-6031')
        period=Activity.objects.create(title='不变的活动',status='active',created_by=admin,
            start_at=self.now-timedelta(days=1),started_at=self.now-timedelta(days=1),scheduled_end_at=self.now+timedelta(days=2))
        work=self.works[0]
        snap=ActivityWork.objects.create(activity=period,work=work,snapshot_title=work.display_title)
        rec=Recommendation.objects.create(activity=period,activity_work=snap,work=work,scope='activity',anonymous_client=self.people[0])
        Recommendation.objects.filter(pk=rec.pk).update(created_at=self.now-timedelta(days=40))
        for path in (f'/activities/{period.pk}/',f'/api/v1/activities/{period.pk}/'):
            self.assertEqual(self.get(path,{'period':'all'}).status_code,400)
            self.assertEqual(self.get(path,{'period':'week'}).status_code,400)
            response=self.get(path,{'sort':'likes'})
            self.assertEqual(response.status_code,200)
        result=activities.listing(period.pk)
        self.assertNotIn('period',result)
        self.assertEqual(result['results'][0]['recommendation_count'],1)
        response=self.get(f'/activities/{period.pk}/')
        self.assertNotContains(response,'公共书单时间范围')
        for option in response.context['sort_options']:
            self.assertNotIn('period',parse_qs(urlsplit(option['url']).query))
