import json
from datetime import timedelta
from unittest.mock import patch
from django.db import OperationalError
from django.conf import settings
from django.test import Client,TestCase,override_settings
from django.utils import timezone
from booklist import accounts,services
from booklist.models import Activity,ActivityNoticeSeen,Notification,PublicRecommendation,PublicLike,Recommendation,ReasonLike


@override_settings(REQUIRE_ACCOUNT=True)
class ActivityNoticeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tokens={};cls.people={}
        for name in ('noticealice','noticebob'):
            token,session=accounts.register(name,name,'Activity-Notice!Testing2026')
            cls.tokens[name]=token;cls.people[name]=session.account
        cls.admin=services.initialize_admin('noticeadmin','Activity-Notice!Admin2026')
        now=timezone.now();cls.now=now
        cls.active=Activity.objects.create(title='当前主题',status='active',created_by=cls.admin,
            start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        cls.upcoming=Activity.objects.create(title='下期主题',status='scheduled',created_by=cls.admin,
            start_at=now+timedelta(days=2),scheduled_end_at=now+timedelta(days=3))

    def browser(self,name='noticealice'):
        client=Client(enforce_csrf_checks=True)
        if name:client.cookies[settings.ACCOUNT_COOKIE]=self.tokens[name]
        return client

    def mark(self,client,ids,account_id=None):
        csrf=client.get('/api/v1/csrf/').json()['csrf_token']
        return client.post('/api/v1/activities/notices/seen/',json.dumps({'ids':ids,
            'account_id':account_id or self.people['noticealice'].pk}),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)

    def feed(self,client):return client.get('/api/v1/activities/notices/')

    def test_first_visit_published_only_and_no_private_fields(self):
        for status in ('draft','cancelled','ended'):
            now=self.now
            Activity.objects.create(title='不应泄露主题',theme='草稿内容',status=status,created_by=self.admin,
                start_at=now-timedelta(days=4),scheduled_end_at=now-timedelta(days=2),
                started_at=now-timedelta(days=4) if status=='ended' else None,
                ended_at=now-timedelta(days=2) if status=='ended' else None,
                cancelled_at=now-timedelta(days=3) if status=='cancelled' else None)
        response=self.feed(self.browser())
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'activity_ids':[self.upcoming.pk,self.active.pk],
            'unseen_ids':[self.upcoming.pk,self.active.pk],'account_key':str(self.people['noticealice'].pk)})
        self.assertNotIn('草稿内容',response.content.decode())
        self.assertEqual(response['Cache-Control'],'no-store')
        self.assertEqual(self.feed(self.browser(None)).json()['account_key'],'guest')

    def test_visit_is_per_account_idempotent_and_follows_new_session(self):
        client=self.browser()
        # GET is read-only, including rendering the target page before JS acknowledgement.
        self.assertEqual(client.get(f'/activities/{self.active.pk}/').status_code,200)
        self.assertFalse(ActivityNoticeSeen.objects.exists())
        ids=[self.active.pk,self.upcoming.pk]
        for _ in range(2):self.assertEqual(self.mark(client,ids).json()['unseen_ids'],[])
        self.assertEqual(ActivityNoticeSeen.objects.count(),2)
        token,session=accounts.session(self.people['noticealice'])
        self.tokens['noticealice']=token
        self.assertEqual(self.feed(self.browser()).json()['unseen_ids'],[])
        self.assertEqual(set(self.feed(self.browser('noticebob')).json()['unseen_ids']),set(ids))
        self.assertEqual(set(self.feed(self.browser(None)).json()['unseen_ids']),set(ids))

    def test_new_publication_reappears_but_updates_pause_resume_do_not(self):
        client=self.browser();ids=[self.active.pk,self.upcoming.pk];self.mark(client,ids)
        Activity.objects.filter(pk=self.active.pk).update(title='修改标题',status='paused')
        self.assertEqual(self.feed(client).json()['unseen_ids'],[])
        Activity.objects.filter(pk=self.active.pk).update(status='active')
        self.assertEqual(self.feed(client).json()['unseen_ids'],[])
        draft=Activity.objects.create(title='新一期',status='draft',created_by=self.admin,
            start_at=self.now+timedelta(days=5),scheduled_end_at=self.now+timedelta(days=6))
        self.assertEqual(self.feed(client).json()['unseen_ids'],[])
        # A stale page snapshot cannot acknowledge a publication made after page load.
        Activity.objects.filter(pk=draft.pk).update(status='scheduled')
        self.assertEqual(self.mark(client,ids).json()['unseen_ids'],[draft.pk])

    def test_expired_cancelled_or_empty_calendar_never_hints(self):
        Activity.objects.filter(pk=self.active.pk).update(scheduled_end_at=self.now-timedelta(minutes=1))
        Activity.objects.filter(pk=self.upcoming.pk).update(status='cancelled',cancelled_at=self.now)
        self.assertEqual(self.feed(self.browser()).json()['activity_ids'],[])
        self.assertEqual(self.feed(self.browser()).json()['unseen_ids'],[])

    def test_authentication_csrf_stale_account_and_invalid_ids(self):
        self.assertEqual(self.mark(self.browser(None),[self.active.pk]).status_code,401)
        self.assertEqual(self.mark(self.browser('noticebob'),[self.active.pk]).status_code,409)
        client=self.browser()
        self.assertEqual(client.post('/api/v1/activities/notices/seen/', '{}',content_type='application/json').status_code,403)
        for ids in ([True],[0],[-1],['1'],[2**63],[1]*101):
            self.assertEqual(self.mark(client,ids).status_code,400)
        self.assertFalse(ActivityNoticeSeen.objects.exists())
        self.assertEqual(self.feed(client).status_code,200)
        self.assertEqual(client.get('/api/v1/activities/notices/?other=1').status_code,400)

    def test_seen_request_does_not_touch_interactions_or_mark_unknown_drafts(self):
        models=(Notification,PublicRecommendation,PublicLike,Recommendation,ReasonLike)
        before={model:list(model.objects.values()) for model in models}
        draft=Activity.objects.create(title='草稿',created_by=self.admin,start_at=self.now,
            scheduled_end_at=self.now+timedelta(days=1))
        self.assertEqual(self.mark(self.browser(),[draft.pk,999999,self.active.pk]).status_code,200)
        self.assertEqual(list(ActivityNoticeSeen.objects.values_list('activity_id',flat=True)),[self.active.pk])
        self.assertEqual(before,{model:list(model.objects.values()) for model in models})

    def test_header_markup_has_nonmodal_cue_and_marks_only_theme_context(self):
        client=self.browser()
        home=client.get('/')
        self.assertContains(home,'data-activity-entry')
        self.assertContains(home,'data-activity-notice role="status" hidden')
        self.assertContains(home,'activity-notice-state')
        self.assertContains(home,'activity-notice.mjs')
        self.assertNotContains(home,'data-activity-section="true"')
        for path in (f'/activities/{self.active.pk}/','/activities/history/'):
            self.assertContains(client.get(path),'data-activity-section="true"')

    def test_optional_header_cue_does_not_break_database_error_page(self):
        from django.test import RequestFactory
        from booklist.activity_notices import header_state
        request=RequestFactory().get('/')
        self.assertIsNone(header_state(request))
        request.account=None
        with patch('booklist.activity_notices.state',side_effect=OperationalError('synthetic unavailable')):
            self.assertIsNone(header_state(request))
