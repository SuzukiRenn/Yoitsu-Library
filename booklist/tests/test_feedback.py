import json
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone
from unittest.mock import patch
from booklist import services
from booklist.models import Feedback, Work, PublicRecommendation, PublicLike, AdminAuditLog


class FeedbackTests(TestCase):
    def setUp(self):
        self.owner = services.initialize_admin('feedback-owner', 'Synthetic-Admin!Pass-123')
        admin_token, _ = services.login('feedback-owner', 'Synthetic-Admin!Pass-123')
        self.admin = Client(enforce_csrf_checks=True)
        self.admin.cookies[settings.ADMIN_COOKIE] = admin_token
        self.admin.get('/api/v1/csrf/')
        self.browser, self.person = self.visitor()
        self.other, _ = self.visitor()
        self.work = Work.objects.create(original_submitted_title='反馈测试作品')
        self.payload = {'category': 'metadata', 'message': '作者有误，请核对', 'page_url': f'/works/{self.work.pk}/#reasons'}

    def visitor(self):
        token, person = services.issue_anonymous()
        browser = Client(enforce_csrf_checks=True)
        browser.cookies[settings.ANONYMOUS_COOKIE] = token
        browser.get('/api/v1/csrf/')
        return browser, person

    def post(self, browser, path='/api/v1/feedback/', data=None, key='feedback-test-key-0001'):
        return browser.post(path, json.dumps(data if data is not None else self.payload), content_type='application/json',
                            HTTP_X_CSRFTOKEN=browser.cookies['csrftoken'].value, HTTP_IDEMPOTENCY_KEY=key)

    def test_public_entry_context_and_private_history(self):
        self.assertContains(self.browser.get('/'), '问题反馈')
        self.assertContains(self.browser.get(f'/works/{self.work.pk}/'), f'/feedback/?from=/works/{self.work.pk}/')
        self.assertContains(self.browser.get('/activities/'), '/feedback/?from=/activities/')
        self.assertEqual(self.post(self.browser).status_code, 200)
        row = Feedback.objects.get()
        self.assertEqual(row.anonymous_client, self.person)
        self.assertContains(self.browser.get('/feedback/'), '作者有误，请核对')
        self.assertNotContains(self.other.get('/feedback/'), '作者有误，请核对')
        self.assertNotContains(Client().get('/feedback/'), '作者有误，请核对')
        self.assertEqual(self.browser.get('/api/v1/feedback/').status_code, 405)
        self.assertEqual(self.browser.get(f'/feedback/?id={row.pk}').status_code, 400)
        self.assertEqual(self.browser.get('/feedback/')['Cache-Control'], 'no-store')

    def test_idempotent_retry_preserves_single_report(self):
        first = self.post(self.browser)
        self.assertEqual(first.json(), self.post(self.browser).json())
        self.assertEqual(Feedback.objects.count(), 1)
        changed = {**self.payload, 'message': '改了内容'}
        self.assertEqual(self.post(self.browser, data=changed).status_code, 409)
        self.assertEqual(Feedback.objects.count(), 1)

    def test_csrf_identity_and_admin_authorization(self):
        self.assertEqual(self.browser.post('/api/v1/feedback/', json.dumps(self.payload), content_type='application/json').status_code, 403)
        guest = Client(enforce_csrf_checks=True)
        guest.get('/api/v1/csrf/')
        self.assertEqual(self.post(guest).status_code, 401)
        self.post(self.browser)
        row = Feedback.objects.get()
        self.assertRedirects(self.browser.get(f'/manage/feedback/{row.pk}/'), '/manage/login/', fetch_redirect_response=False)
        data = {'status': 'resolved', 'reply': '已核对', 'revision': 1}
        self.assertEqual(self.post(self.browser, f'/api/v1/admin/feedback/{row.pk}/', data).status_code, 401)
        row.refresh_from_db(); self.assertEqual(row.status, 'pending')

    def test_validation_and_unsafe_context_urls(self):
        for field, values in {'message': ['', ' \n ', '字' * 1001], 'category': ['unknown'],
                              'page_url': ['https://evil.example/', '//evil.example/', '/\\evil.example/', '/manage/', '/api/v1/csrf/', '/works/1/\n', '/works/1/'+'x'*1000]}.items():
            for value in values:
                with self.subTest(field=field, value=value[:40]):
                    self.assertEqual(self.post(self.browser, data={**self.payload, field: value}).status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)
        for source in ('https://evil.example/', '//evil.example/', '/manage/'):
            self.assertEqual(self.browser.get('/feedback/', {'from': source}).status_code, 400)

    def test_admin_reply_is_private_audited_and_does_not_moderate(self):
        rec = PublicRecommendation.objects.create(work=self.work, anonymous_client=self.person)
        like = PublicLike.objects.create(work=self.work, anonymous_client=self.person)
        self.post(self.browser)
        row = Feedback.objects.get()
        self.assertContains(self.admin.get('/manage/?section=feedback'), '待处理 1 条')
        self.assertContains(self.admin.get(f'/manage/feedback/{row.pk}/'), f'/manage/works/{self.work.pk}/')
        data = {'status': 'resolved', 'reply': '已核查并修正', 'revision': 1}
        self.assertEqual(self.post(self.admin, f'/api/v1/admin/feedback/{row.pk}/', data).status_code, 200)
        row.refresh_from_db(); self.assertEqual(row.handled_by, self.owner)
        self.assertEqual(row.revision, 2)
        self.assertContains(self.browser.get('/feedback/'), '已核查并修正')
        self.assertNotContains(self.other.get('/feedback/'), '已核查并修正')
        audit = AdminAuditLog.objects.get(action='feedback.handle')
        self.assertEqual(audit.before_data['status'], 'pending')
        self.assertEqual(audit.after_data['status'], 'resolved')
        rec.refresh_from_db(); like.refresh_from_db(); self.work.refresh_from_db()
        self.assertIsNone(rec.invalidated_at); self.assertIsNone(like.invalidated_at); self.assertIsNone(self.work.hidden_at)
        self.assertEqual(self.post(self.admin, f'/api/v1/admin/feedback/{row.pk}/', data).status_code, 409)
        self.assertEqual(AdminAuditLog.objects.filter(action='feedback.handle').count(), 1)

    def test_escaped_content_and_required_admin_explanation(self):
        self.post(self.browser, data={**self.payload, 'message': '<script>alert(1)</script>'})
        row = Feedback.objects.get()
        for browser, path in ((self.browser, '/feedback/'), (self.admin, '/manage/?section=feedback'), (self.admin, f'/manage/feedback/{row.pk}/')):
            response = browser.get(path)
            self.assertContains(response, '&lt;script&gt;')
            self.assertNotContains(response, '<script>alert(1)</script>')
        for data in ({'status': 'resolved', 'reply': '', 'revision': 1}, {'status': 'invalid', 'reply': '原因', 'revision': 1}):
            self.assertEqual(self.post(self.admin, f'/api/v1/admin/feedback/{row.pk}/', data).status_code, 400)

    def test_pending_first_and_feedback_limits(self):
        closed = Feedback.objects.create(anonymous_client=self.person, category='other', message='历史已完成', status='resolved')
        pending = Feedback.objects.create(anonymous_client=self.person, category='other', message='待核查内容')
        response = self.admin.get('/manage/?section=feedback')
        self.assertEqual(response.context['rows'][0].pk, pending.pk)
        # Keep this batch in one fixed hourly bucket, even across a real hour boundary.
        with patch('booklist.services.time.time', return_value=timezone.now().timestamp()) as clock:
            for number in range(20):
                self.assertEqual(self.post(self.browser, key=f'feedback-limit-{number:04d}').status_code, 200)
            self.assertEqual(self.post(self.browser, key='feedback-limit-blocked').status_code, 429)
            self.assertEqual(Feedback.objects.count(), 22)
            clock.return_value += 3600
            self.assertEqual(self.post(self.browser, key='feedback-limit-next-hour').status_code, 200)
        self.assertEqual(Feedback.objects.count(), 23)
        self.assertNotContains(self.browser.get('/feedback/'), closed.message)
