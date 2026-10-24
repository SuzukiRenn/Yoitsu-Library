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
        self.assertContains(response, '公共书架暂无作品')
        self.assertContains(response, '← 返回书架')
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_no_filters_or_writes(self):
        self.work()
        self.assertEqual(self.client.get('/random-book/?q=不存在').status_code, 400)
        self.assertEqual(self.client.post('/random-book/').status_code, 405)

    def test_excludes_current_but_keeps_the_other_eligible_book(self):
        current=self.work(); other=self.work()
        response=self.client.get(f'/random-book/?exclude={current.pk}')
        self.assertRedirects(response,f'/works/{other.pk}/',fetch_redirect_response=False)
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_only_current_book_explains_instead_of_redirecting(self):
        current=self.work()
        response=self.client.get(f'/random-book/?exclude={current.pk}')
        self.assertContains(response,'公共书架里暂时只有这一本')
        self.assertContains(response,f'href="/works/{current.pk}/"')
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_nonpublic_current_does_not_exclude_the_single_public_candidate(self):
        current=Work.objects.create(original_submitted_title='不在公共书架')
        other=self.work()
        self.assertRedirects(self.client.get(f'/random-book/?exclude={current.pk}'),f'/works/{other.pk}/',fetch_redirect_response=False)
        PublicRecommendation.objects.filter(work=other).update(withdrawn_at=timezone.now())
        self.assertContains(self.client.get(f'/random-book/?exclude={current.pk}'),'公共书架暂无作品')

    def test_exclusion_parameter_validation_and_detail_link(self):
        current=self.work()
        for value in ['', '0', '-1', '01', 'abc', '9'*19, '1&exclude=2']:
            self.assertEqual(self.client.get('/random-book/?exclude='+value).status_code,400)
        self.assertContains(self.client.get(f'/works/{current.pk}/'),f'href="/random-book/?exclude={current.pk}"')


class ActivityRandomBookTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from booklist.models import Activity
        _, self.person = services.issue_anonymous()
        admin = services.initialize_admin('owner', 'Monthly-Booklist!Testing-Password-49')
        now = timezone.now()
        self.period = Activity.objects.create(title='随机主题', status='active', created_by=admin,
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1))

    def entry(self, **fields):
        from booklist.models import ActivityWork, Recommendation
        work = Work.objects.create(original_submitted_title='主题作品', **fields)
        snap = ActivityWork.objects.create(activity=self.period, work=work, snapshot_title=work.display_title)
        rec = Recommendation.objects.create(scope='activity', activity=self.period, activity_work=snap, work=work, anonymous_client=self.person)
        return work, snap, rec

    def test_scope_and_current_exclusion_survive_detail_navigation(self):
        current,_,_ = self.entry(); chosen,_,_ = self.entry()
        other = Work.objects.create(original_submitted_title='仅公共', public_added_at=timezone.now())
        PublicRecommendation.objects.create(work=other,anonymous_client=self.person)
        response=self.client.get(f'/random-book/?activity={self.period.pk}&exclude={current.pk}')
        self.assertRedirects(response,f'/works/{chosen.pk}/?activity={self.period.pk}',fetch_redirect_response=False)
        page=self.client.get(f'/works/{chosen.pk}/?activity={self.period.pk}')
        self.assertContains(page,f'/random-book/?exclude={chosen.pk}&amp;activity={self.period.pk}')
        shelf=self.client.get(f'/activities/{self.period.pk}/')
        self.assertContains(shelf,f'/random-book/?activity={self.period.pk}')
        self.assertContains(shelf,'data-shelf-view-toggle')
        self.assertContains(self.client.get('/'),'data-shelf-view-toggle')

    def test_ineligible_activity_entries_are_not_drawn(self):
        chosen,_,_=self.entry()
        self.entry(hidden_at=timezone.now()); self.entry(review_status='pending'); self.entry(merged_into=chosen)
        _,snap,rec=self.entry();rec.archived_at=timezone.now();rec.save();snap.archived_at=timezone.now();snap.save()
        _,_,rec=self.entry();rec.withdrawn_at=timezone.now();rec.save()
        _,_,rec=self.entry();rec.invalidated_at=timezone.now();rec.save()
        self.assertRedirects(self.client.get(f'/random-book/?activity={self.period.pk}'),f'/works/{chosen.pk}/?activity={self.period.pk}',fetch_redirect_response=False)

    def test_empty_and_single_do_not_fall_back_to_public(self):
        response=self.client.get(f'/random-book/?activity={self.period.pk}')
        self.assertContains(response,'这个主题暂无作品')
        current,_,_=self.entry()
        response=self.client.get(f'/random-book/?activity={self.period.pk}&exclude={current.pk}')
        self.assertContains(response,'这个主题里暂时只有这一本')
        self.assertContains(response,f'/works/{current.pk}/?activity={self.period.pk}')
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_historical_snapshots_can_include_retired_public_works(self):
        work,_,_=self.entry(source_metadata={'manual_retired_at':'2026-10-23'})
        self.period.status='ended';self.period.ended_at=timezone.now();self.period.save()
        self.assertRedirects(self.client.get(f'/random-book/?activity={self.period.pk}'),f'/works/{work.pk}/?activity={self.period.pk}',fetch_redirect_response=False)

    def test_invalid_or_unpublished_activity_is_rejected(self):
        for value in ['', '0', '01', 'abc', '9'*19, '1&activity=2']:
            self.assertEqual(self.client.get('/random-book/?activity='+value).status_code,400)
        self.period.status='draft';self.period.started_at=None;self.period.save()
        self.assertEqual(self.client.get(f'/random-book/?activity={self.period.pk}').status_code,404)
