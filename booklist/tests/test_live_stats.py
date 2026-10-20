from datetime import timedelta
from django.conf import settings
from django.test import TestCase, Client
from django.utils import timezone
from booklist import activities, public_booklist as books, services
from booklist.models import Activity, PublicLike, PublicRecommendation, Work


class LiveStatsTests(TestCase):
    def setUp(self):
        admin = services.initialize_admin('live-owner', 'Live-Stats!Testing-Password-49')
        now = timezone.now()
        self.activity = Activity.objects.create(title='即时推荐', start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(hours=1),
            status='active', created_by=admin)
        self.work = Work.objects.create(original_submitted_title='实时作品', public_added_at=now)
        token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token
        _, self.other = services.issue_anonymous()

    def stats(self, activity=False, browser=None):
        prefix = f'/api/v1/activities/{self.activity.pk}' if activity else '/api/v1/public'
        return (browser or self.client).get(prefix+'/stats/', {'ids': self.work.pk})

    def submit_activity(self):
        activities.submit(self.activity.pk, self.person, {'work_id': self.work.pk, 'title': self.work.display_title,
            'reason': '推荐理由', 'confirmed_unmatched': False, 'copy_public_reason': False})

    def test_other_visitors_changes_and_own_state_are_independent(self):
        PublicRecommendation.objects.create(work=self.work, anonymous_client=self.person)
        self.assertEqual(self.stats().json()['results'][0]['like_count'], 0)
        PublicLike.objects.create(work=self.work, anonymous_client=self.other)
        row = self.stats().json()['results'][0]
        self.assertEqual((row['recommendation_count'], row['like_count'], row['recommended'], row['liked']), (1, 1, True, False))
        row = self.stats(browser=Client()).json()['results'][0]
        self.assertFalse(row['recommended'])
        self.assertNotIn('title', row)  # Polling does not ship metadata/covers or rebuild the shelf.

    def test_activity_counts_are_separate_and_zero_entry_can_only_rejoin_via_form(self):
        self.submit_activity()
        activities.like(self.activity.pk, self.work.pk, self.other, True)
        row = self.stats(True).json()['results'][0]
        self.assertEqual((row['recommendation_count'], row['like_count'], row['reason_preview']), (1, 1, '推荐理由'))
        activities.withdraw(self.activity.pk, self.work.pk, self.person)
        self.assertEqual(self.stats(True).json()['results'][0], {'id':self.work.pk,'available':False})
        self.assertEqual(activities.listing(self.activity.pk)['total'],0)
        self.assertEqual(self.stats().json()['results'][0]['recommendation_count'], 1)

    def test_hidden_pending_merged_and_unenrolled_are_not_exposed(self):
        for changes in ({'hidden_at': timezone.now()}, {'review_status': 'pending'}, {'public_added_at': None}):
            Work.objects.filter(pk=self.work.pk).update(**changes)
            self.assertEqual(self.stats().json()['results'], [{'id': self.work.pk, 'available': False}])
            Work.objects.filter(pk=self.work.pk).update(hidden_at=None, review_status='clear', public_added_at=timezone.now())
        self.assertEqual(self.stats(True).json()['results'][0], {'id': self.work.pk, 'available': False})
        target = Work.objects.create(original_submitted_title='合并目标')
        Work.objects.filter(pk=self.work.pk).update(merged_into=target)
        self.assertFalse(self.stats().json()['results'][0]['available'])

    def test_validation_and_moderated_reasons(self):
        path = '/api/v1/public/stats/'
        for query in ({}, {'ids': '0'}, {'ids': '1,,2'}, {'ids': '-1'}, {'ids': '01'},
                      {'ids': ','.join(map(str, range(1, 102)))}, {'ids': '1', 'extra': 'x'}):
            self.assertEqual(self.client.get(path, query).status_code, 400)
        self.assertEqual(self.client.get(path+'?ids=1&ids=2').status_code, 400)
        self.submit_activity()
        activities.recs(self.activity.pk).update(text_hidden_at=timezone.now())
        self.assertEqual(self.stats(True).json()['results'][0]['reason_preview'], '')

    def test_activity_write_returns_confirmed_counts_and_readonly_snapshot(self):
        import json, uuid
        self.submit_activity()
        path = f'/api/v1/activities/{self.activity.pk}/works/{self.work.pk}/like/'
        response = self.client.post(path, json.dumps({'enabled': True}), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['work']['like_count'], 1)
        self.assertTrue(response.json()['work']['liked'])
        Activity.objects.filter(pk=self.activity.pk).update(status='paused')
        self.assertFalse(self.stats(True).json()['results'][0]['writable'])
