import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.core.management import call_command
from django.db import OperationalError
from booklist import accounts, services, activities, merging, public_booklist as books
from booklist.models import (Account, AccountSession, AdminAuditLog, AnonymousClient,
    Work, PublicRecommendation, PublicLike, Activity, Notification, WorkLikeEvent, ReasonLike)

PASSWORD = 'Reader-Testing!Password-6021'


@override_settings(REQUIRE_ACCOUNT=True)
class AccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tokens, cls.readers = {}, {}
        for name, label in [('alice', '青禾'), ('bob', '林间'), ('carol', '小满')]:
            token, session = accounts.register(name, label, PASSWORD)
            cls.tokens[name], cls.readers[name] = token, session.account
        cls.admin = services.initialize_admin('owner', 'Admin-Testing!Password-6021')
        cls.work = Work.objects.create(wenku8_book_id=1001, identity_status='matched', wenku8_title='合成测试作品', public_added_at=timezone.now())
        now = timezone.now()
        cls.activity = Activity.objects.create(title='合成推荐活动', status='active', created_by=cls.admin,
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1))

    def browser(self, name=None):
        client = Client(enforce_csrf_checks=True)
        if name:
            client.cookies[settings.ACCOUNT_COOKIE] = self.tokens[name]
        return client

    def post(self, client, path, data=None, key=None):
        csrf = client.get('/api/v1/csrf/').json()['csrf_token']
        return client.post(path, json.dumps(data or {}), content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def relation(self, name, kind, enabled=True, activity=False, key=None, client=None):
        prefix = f'/api/v1/activities/{self.activity.pk}' if activity else '/api/v1/public'
        return self.post(client or self.browser(name), f'{prefix}/works/{self.work.pk}/{kind}/', {'enabled': enabled}, key)

    def recommend(self, name, activity=False):
        if activity:
            return activities.submit(self.activity.pk, self.readers[name].principal, {'work_id': self.work.pk,
                'title': self.work.display_title, 'reason': '', 'confirmed_unmatched': False, 'copy_public_reason': False})
        books.set_relation(self.work, self.readers[name].principal, 'recommend', True)

    def test_registration_uses_dedicated_principal_and_hashed_secrets(self):
        visitor = self.browser()
        old_token, old_client = services.issue_anonymous()
        PublicRecommendation.objects.create(work=self.work, anonymous_client=old_client)
        visitor.cookies[settings.ANONYMOUS_COOKIE] = old_token
        response = self.post(visitor, '/api/v1/account/register/',
            {'username': 'NEW_READER', 'display_name': '新读者', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, response.content)
        account = Account.objects.get(username='new_reader')
        self.assertNotEqual(account.principal_id, old_client.pk)
        self.assertTrue(check_password(PASSWORD, account.password_hash))
        token = visitor.cookies[settings.ACCOUNT_COOKIE].value
        self.assertNotEqual(AccountSession.objects.get(account=account).token_hash, token)
        self.assertTrue(response.cookies[settings.ACCOUNT_COOKIE]['httponly'])
        self.assertEqual(response.cookies[settings.ACCOUNT_COOKIE]['samesite'], 'Lax')
        self.assertFalse(PublicRecommendation.objects.filter(anonymous_client=account.principal).exists())
        self.assertEqual(self.relation(None, 'recommend', False, client=visitor).status_code, 200)
        self.assertTrue(books.recommendations().filter(anonymous_client=old_client).exists())
        self.assertNotIn('password', json.dumps(response.json()))

    def test_registration_validation_duplicate_case_and_csrf(self):
        visitor = self.browser()
        path = '/api/v1/account/register/'
        self.assertEqual(visitor.post(path, '{}', content_type='application/json').status_code, 403)
        for name, secret in [('a', PASSWORD), ('含空格 名', PASSWORD), ('valid', '123456789012'), ('valid', 'short')]:
            self.assertEqual(self.post(visitor, path, {'username': name, 'display_name': '读者', 'password': secret}).status_code, 400)
        self.assertEqual(self.post(visitor, path, {'username': 'ALICE', 'display_name': '另一位', 'password': PASSWORD}).status_code, 409)
        # With scripts unavailable, credentials must never default to URL query submission.
        for url in ('/account/login/', '/account/register/', '/manage/login/'):
            self.assertContains(visitor.get(url), '<form method="post"')

    def test_display_names_can_repeat_but_handle_cannot_and_controls_rejected(self):
        client = self.browser('bob')
        self.assertEqual(self.post(client, '/api/v1/account/profile/', {'display_name': '青禾'}).status_code, 200)
        self.assertEqual(Account.objects.get(username='bob').display_name, '青禾')
        profile = client.get('/account/')
        self.assertContains(profile, '保存昵称')
        self.assertContains(profile, '<strong>bob</strong>', html=True)
        self.assertNotContains(client.get('/'), '@bob')
        self.assertEqual(self.post(client, '/api/v1/account/profile/', {'display_name': '\u200b'}).status_code, 400)
        self.assertEqual(self.post(client, '/api/v1/account/profile/', {'display_name': '好', 'username': 'alice'}).status_code, 400)

    def test_visitor_can_read_but_cookie_cannot_grant_account_or_admin_permissions(self):
        self.recommend('alice')
        client = self.browser()
        token, _ = services.issue_anonymous()
        client.cookies[settings.ANONYMOUS_COOKIE] = token
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(self.post(client, '/api/v1/identity/').status_code, 401)
        self.assertEqual(self.relation(None, 'like', client=client).status_code, 401)
        self.assertEqual(self.post(client, '/api/v1/identity/reset/').status_code, 409)
        self.assertEqual(client.get('/api/v1/notifications/').status_code, 401)
        self.assertEqual(client.get('/recommend/').status_code, 302)
        client.cookies[settings.ADMIN_COOKIE] = self.tokens['alice']
        self.assertEqual(client.get('/api/v1/admin/me/').status_code, 401)
        client.cookies[settings.ACCOUNT_COOKIE] = token
        self.assertEqual(client.get('/api/v1/account/me/').status_code, 401)

    def test_account_cookies_are_secure_in_production_mode(self):
        with override_settings(COOKIE_SECURE=True):
            response = self.post(self.browser(), '/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD})
        self.assertTrue(response.cookies[settings.ACCOUNT_COOKIE]['secure'])

    def test_login_rotation_logout_and_cross_device_ownership(self):
        self.recommend('alice')
        first = self.browser('alice')
        old = self.tokens['alice']
        self.assertEqual(self.post(first, '/api/v1/account/login/', {'username': 'ALICE', 'password': PASSWORD}).status_code, 200)
        stolen = self.browser(); stolen.cookies[settings.ACCOUNT_COOKIE] = old
        self.assertEqual(stolen.get('/api/v1/account/me/').status_code, 401)
        second = self.browser()
        self.post(second, '/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD})
        row = second.get('/api/v1/public/works/').json()['results'][0]
        self.assertTrue(row['recommended'])
        self.assertEqual(self.relation(None, 'recommend', client=second).json()['work']['recommendation_count'], 1)
        self.post(first, '/api/v1/account/logout/')
        self.assertEqual(first.get('/api/v1/account/me/').status_code, 401)
        self.assertEqual(second.get('/api/v1/account/me/').status_code, 200)
        self.assertEqual(self.post(first, '/api/v1/account/logout/').status_code, 200)

    def test_expired_and_version_invalid_sessions_cannot_write(self):
        self.recommend('bob')
        AccountSession.objects.filter(account=self.readers['alice']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.relation('alice', 'like').status_code, 401)
        Account.objects.filter(pk=self.readers['carol'].pk).update(session_version=2)
        self.assertEqual(self.relation('carol', 'like').status_code, 401)

    def test_password_change_revokes_every_session_and_requires_current_password(self):
        client = self.browser('alice')
        other = self.browser()
        self.post(other, '/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD})
        self.assertEqual(self.post(client, '/api/v1/account/password/', {'current_password': 'wrong', 'new_password': PASSWORD+'new'}).status_code, 403)
        self.assertEqual(self.post(client, '/api/v1/account/password/', {'current_password': PASSWORD, 'new_password': PASSWORD+'new'}).status_code, 200)
        self.assertFalse(AccountSession.objects.filter(account=self.readers['alice']).exists())
        self.assertEqual(other.get('/api/v1/account/me/').status_code, 401)
        self.assertEqual(self.post(other, '/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD}).status_code, 401)

    def test_login_rate_limits_failed_credentials(self):
        client = self.browser()
        for _ in range(10):
            self.assertEqual(self.post(client, '/api/v1/account/login/', {'username': 'alice', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.post(client, '/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD}).status_code, 429)

    def test_first_and_co_recommendation_withdrawal_rejoin_and_live_snapshot(self):
        self.recommend('alice')
        response = self.relation('bob', 'recommend')
        self.assertEqual(response.json()['work']['first_recommender']['username'], 'alice')
        self.assertEqual(response.json()['work']['co_recommenders'][0]['username'], 'bob')
        self.relation('alice', 'recommend', False)
        row = self.browser('bob').get('/api/v1/public/stats/', {'ids': self.work.pk}).json()['results'][0]
        self.assertTrue(row['first_recommender']['withdrawn'])
        self.assertEqual(row['recommendation_count'], 1)
        row = self.relation('alice', 'recommend').json()['work']
        self.assertFalse(row['first_recommender']['withdrawn'])
        page = self.browser('bob').get('/')
        self.assertNotContains(page, 'data-recommenders')
        detail = self.browser('bob').get(f'/works/{self.work.pk}/')
        self.assertContains(detail, '首次推荐')
        self.assertContains(detail, '青禾')
        self.assertNotContains(detail, '@alice')
        self.assertNotContains(detail, '@bob')
        self.assertContains(detail, '共同推荐')

    def test_work_likes_notify_all_current_recommenders_once_except_self(self):
        self.recommend('alice'); self.recommend('bob')
        key = str(uuid.uuid4())
        self.assertEqual(self.relation('carol', 'like', key=key).status_code, 200)
        self.relation('carol', 'like', key=key)
        self.relation('carol', 'like', False)
        self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(WorkLikeEvent.objects.count(), 1)
        self.relation('alice', 'like')
        self.assertEqual(Notification.objects.filter(recipient=self.readers['alice']).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.readers['bob']).count(), 2)

    def test_withdrawn_and_late_recommenders_do_not_receive_old_like(self):
        self.recommend('alice'); self.recommend('bob')
        self.relation('bob', 'recommend', False)
        self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 1)
        self.relation('bob', 'recommend')
        self.relation('carol', 'like', False); self.relation('carol', 'like')
        self.assertFalse(Notification.objects.filter(recipient=self.readers['bob']).exists())

    def test_dedup_survives_zero_cycle_and_never_changes_like_count(self):
        self.recommend('alice')
        self.relation('carol', 'like')
        self.relation('alice', 'recommend', False)
        self.relation('alice', 'recommend')
        self.assertEqual(self.relation('carol', 'like').json()['work']['like_count'], 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_public_and_activity_notification_scopes_are_separate(self):
        self.recommend('alice', True); self.recommend('bob', True)
        self.relation('carol', 'like', activity=True)
        self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 4)
        self.assertEqual(set(WorkLikeEvent.objects.values_list('scope_key', flat=True)), {'public', f'activity:{self.activity.pk}'})
        self.relation('carol', 'like', False, activity=True); self.relation('carol', 'like', activity=True)
        self.assertEqual(Notification.objects.count(), 4)
        self.assertEqual(books.serialize([self.work])[0]['recommendation_count'], 2)
        self.assertEqual(activities.listing(self.activity.pk)['results'][0]['co_recommenders'][0]['username'], 'bob')
        browser = self.browser('alice')
        self.assertNotContains(browser.get(f'/activities/{self.activity.pk}/'), 'data-recommenders')
        self.assertContains(browser.get(f'/works/{self.work.pk}/?activity={self.activity.pk}'), 'data-recommenders')

    def test_reason_likes_are_separate_and_show_author(self):
        self.recommend('alice')
        books.set_reason(self.work, self.readers['alice'].principal, '合成推荐理由')
        reason = books.reasons().get()
        client = self.browser('bob')
        self.assertEqual(self.post(client, f'/api/v1/public/works/{self.work.pk}/reasons/{reason.pk}/like/', {'enabled': True}).status_code, 200)
        self.assertEqual(ReasonLike.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 0)
        row = books.serialize([self.work])[0]['top_reason']
        self.assertEqual(row['author']['username'], 'alice')
        self.assertEqual(row['like_count'], 1)
        self.assertEqual(PublicLike.objects.count(), 0)
        detail = client.get(f'/works/{self.work.pk}/')
        self.assertContains(detail, '青禾')
        self.assertNotContains(detail, '@alice')

    def test_notification_read_permissions_unread_count_and_idempotence(self):
        self.recommend('alice'); self.recommend('bob'); self.relation('carol', 'like')
        alice, bob = self.browser('alice'), self.browser('bob')
        own = Notification.objects.get(recipient=self.readers['alice'])
        other = Notification.objects.get(recipient=self.readers['bob'])
        response = alice.get('/api/v1/notifications/').json()
        self.assertEqual(response['unread_count'], 1)
        self.assertEqual([row['id'] for row in response['results']], [own.pk])
        self.assertEqual(self.post(alice, '/api/v1/notifications/read/', {'ids': [other.pk]}).json()['changed'], 0)
        self.assertEqual(self.post(alice, '/api/v1/notifications/read/', {'ids': [own.pk]}).json()['changed'], 1)
        self.assertEqual(self.post(alice, '/api/v1/notifications/read/', {'ids': [own.pk]}).json()['changed'], 0)
        self.assertEqual(bob.get('/api/v1/notifications/').json()['unread_count'], 1)
        self.assertEqual(self.post(alice, '/api/v1/notifications/read/', {'ids': [True]}).status_code, 400)
        self.assertEqual(self.post(bob, '/api/v1/notifications/read-all/').json()['changed'], 1)
        page = alice.get('/notifications/')
        self.assertContains(page, '赞了你推荐的')
        self.assertContains(page, '小满')
        self.assertNotContains(page, '@carol')

    def test_no_cross_account_reason_or_metadata_edits_or_identity_spoofing(self):
        alice, bob = self.browser('alice'), self.browser('bob')
        response = self.post(alice, '/api/v1/public/recommendations/', {'work_id': None, 'title': '手工作品', 'reason': '甲理由', 'confirmed_unmatched': True,
            'metadata': {'author': '合成作者', 'description': '合成简介', 'tags': []}})
        work = response.json()['work']
        path = f'/api/v1/catalog/works/{work["id"]}/metadata/'
        self.assertEqual(self.post(bob, path, {'description': '篡改', 'tags': [], 'revision': 1}).status_code, 403)
        self.assertEqual(self.post(bob, f'/api/v1/public/works/{work["id"]}/reason/', {'reason': '乙理由'}).status_code, 409)
        self.assertEqual(self.post(bob, f'/api/v1/public/works/{work["id"]}/recommend/', {'enabled': True}).status_code, 200)
        self.assertEqual(self.post(bob, f'/api/v1/public/works/{work["id"]}/reason/', {'reason': '乙理由'}).status_code, 200)
        self.assertTrue(books.reasons().filter(work_id=work['id'], anonymous_client=self.readers['alice'].principal, reason='甲理由').exists())
        self.assertEqual(self.post(bob, '/api/v1/account/profile/', {'display_name': '伪造', 'account_id': self.readers['alice'].pk}).status_code, 400)

    def test_admin_roles_and_account_disable_restore_audit(self):
        alice = self.browser('alice')
        self.assertNotContains(alice.get('/'), '返回管理后台')
        self.assertEqual(alice.get('/manage/readers/').status_code, 302)
        path = f'/api/v1/admin/readers/{self.readers["alice"].pk}/status/'
        self.assertEqual(self.post(alice, path, {'enabled': False, 'reason': '测试'}).status_code, 401)
        admin = self.browser()
        token, _ = services.login('owner', 'Admin-Testing!Password-6021')
        admin.cookies[settings.ADMIN_COOKIE] = token
        self.assertEqual(admin.get('/manage/readers/').status_code, 200)
        self.assertContains(admin.get('/'), '返回管理后台')
        self.assertEqual(self.post(admin, '/api/v1/identity/').status_code, 401)
        self.assertEqual(self.post(admin, path, {'enabled': False, 'reason': '合成停用测试'}).status_code, 200)
        self.assertEqual(alice.get('/api/v1/account/me/').status_code, 401)
        self.assertEqual(self.post(admin, path, {'enabled': True, 'reason': '合成恢复测试'}).status_code, 200)
        self.assertEqual(alice.get('/api/v1/account/me/').status_code, 401)
        self.assertTrue(AdminAuditLog.objects.filter(action='account.status').exists())

    def test_admin_summary_stays_private_and_contains_no_reason_body(self):
        self.recommend('alice', True)
        activities.reason(self.activity.pk, self.work.pk, self.readers['alice'].principal, '不可导出理由正文')
        path = f'/api/v1/admin/activities/{self.activity.pk}/summary/'
        self.assertEqual(self.browser('alice').get(path).status_code, 401)
        admin = self.browser(); token, _ = services.login('owner', 'Admin-Testing!Password-6021')
        admin.cookies[settings.ADMIN_COOKIE] = token
        response = admin.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('不可导出理由正文', response.content.decode())
        self.assertEqual(response.json()['works'][0]['reason_count'], 1)

    def test_recovery_revokes_sessions_and_preserves_disabled_state(self):
        Account.objects.filter(username='alice').update(is_active=False)
        with patch('getpass.getpass', return_value=PASSWORD+'recovery'):
            call_command('reset_account_password', 'alice', verbosity=0)
        self.assertFalse(Account.objects.get(username='alice').is_active)
        self.assertFalse(AccountSession.objects.filter(account=self.readers['alice']).exists())
        audit = AdminAuditLog.objects.get(action='account.password_reset')
        self.assertNotIn('password_hash', json.dumps(audit.after_data))

    def test_notification_ledger_survives_merge_and_undo(self):
        self.recommend('alice'); self.relation('carol', 'like')
        primary = Work.objects.create(original_submitted_title='合并主作品', public_added_at=timezone.now())
        books.set_relation(primary, self.readers['alice'].principal, 'recommend', True)
        # Source works must remain primary under the existing merge rules.
        plan = merging.plan(self.work.pk, primary.pk)
        operation = merging.merge(self.admin, self.work.pk, primary.pk, {}, plan['version'], '合成合并')
        merging.undo(self.admin, operation.pk, '合成撤销')
        self.relation('carol', 'like', False); self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 1)

    def test_messages_are_paginated_and_profile_html_is_escaped(self):
        for index in range(55):
            work = Work.objects.create(original_submitted_title=f'消息样本{index}')
            event = WorkLikeEvent.objects.create(work=work, actor=self.readers['carol'], scope_key='public')
            Notification.objects.create(event=event, recipient=self.readers['alice'], work_title=work.display_title)
        client = self.browser('alice')
        self.assertEqual(len(client.get('/api/v1/notifications/').json()['results']), 50)
        self.assertEqual(len(client.get('/api/v1/notifications/?page=2').json()['results']), 5)
        self.assertEqual(client.get('/api/v1/notifications/?page=0').status_code, 400)
        self.post(client, '/api/v1/account/profile/', {'display_name': '<script>alert(1)</script>'})
        self.assertNotContains(client.get('/account/'), '<script>alert(1)</script>')

    def test_original_product_cookie_names_are_never_issued_or_deleted(self):
        visitor = self.browser()
        originals = {'booklist_anonymous': 'original-anonymous-value', 'booklist_admin': 'original-admin-value', 'csrftoken': 'original-csrf-value'}
        for name, value in originals.items():
            visitor.cookies[name] = value
        for path, data in [('/api/v1/account/login/', {'username': 'alice', 'password': PASSWORD}),
                           ('/api/v1/account/logout/', {})]:
            response = self.post(visitor, path, data)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(set(response.cookies) & set(originals))
        self.assertEqual({name: visitor.cookies[name].value for name in originals}, originals)
        self.assertTrue(all(name.startswith('identity_booklist_') for name in
            (settings.ACCOUNT_COOKIE, settings.ADMIN_COOKIE, settings.ANONYMOUS_COOKIE, settings.CSRF_COOKIE_NAME)))

    def test_notification_unread_endpoint_is_private_and_tracks_read_state(self):
        self.recommend('alice'); self.relation('carol', 'like')
        client = self.browser('alice')
        self.assertEqual(client.get('/api/v1/notifications/unread/').json(), {'unread_count': 1})
        self.assertEqual(self.browser('bob').get('/api/v1/notifications/unread/').json(), {'unread_count': 0})
        self.assertEqual(self.browser().get('/api/v1/notifications/unread/').status_code, 401)
        self.post(client, '/api/v1/notifications/read-all/')
        self.assertEqual(client.get('/api/v1/notifications/unread/').json(), {'unread_count': 0})

    def test_disabled_recommenders_are_not_notified_and_activity_end_blocks_likes(self):
        self.recommend('alice'); self.recommend('bob')
        Account.objects.filter(username='bob').update(is_active=False)
        self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 1)
        self.recommend('alice', True)
        Activity.objects.filter(pk=self.activity.pk).update(status='ended', ended_at=timezone.now())
        self.assertEqual(self.relation('carol', 'like', activity=True).status_code, 409)
        self.assertEqual(Notification.objects.count(), 1)

    def test_invalidated_recommenders_do_not_receive_new_notifications(self):
        self.recommend('alice'); self.recommend('bob')
        PublicRecommendation.objects.filter(anonymous_client=self.readers['alice'].principal).update(invalidated_at=timezone.now())
        self.relation('carol', 'like')
        self.assertEqual(list(Notification.objects.values_list('recipient__username', flat=True)), ['bob'])

    def test_merging_secondary_like_history_cannot_reset_notification_dedup(self):
        secondary = Work.objects.create(original_submitted_title='合成副作品', public_added_at=timezone.now())
        books.set_relation(secondary, self.readers['alice'].principal, 'recommend', True)
        books.set_relation(secondary, self.readers['carol'].principal, 'like', True)
        self.recommend('alice')
        proposal = merging.plan(self.work.pk, secondary.pk)
        merging.merge(self.admin, self.work.pk, secondary.pk, {}, proposal['version'], '合成通知去重测试')
        self.relation('carol', 'like', False); self.relation('carol', 'like')
        self.assertEqual(Notification.objects.count(), 1)

    def test_notification_and_like_rollback_together_on_database_error(self):
        self.recommend('alice')
        with patch('booklist.notifications.work_liked', side_effect=OperationalError('synthetic unavailable')):
            self.assertEqual(self.relation('carol', 'like').status_code, 503)
        self.assertFalse(PublicLike.objects.exists())
        self.assertFalse(WorkLikeEvent.objects.exists())
        self.assertEqual(self.relation('carol', 'like').status_code, 200)
        self.assertEqual(Notification.objects.count(), 1)

    def test_message_target_checks_recipient_and_resolves_current_merge(self):
        secondary = Work.objects.create(original_submitted_title='消息旧作品', public_added_at=timezone.now())
        books.set_relation(secondary, self.readers['alice'].principal, 'recommend', True)
        books.set_relation(secondary, self.readers['carol'].principal, 'like', True)
        note = Notification.objects.get()
        path = f'/notifications/{note.pk}/work/'
        self.assertEqual(self.browser('bob').get(path).status_code, 404)
        self.recommend('alice')
        proposal = merging.plan(self.work.pk, secondary.pk)
        merging.merge(self.admin, self.work.pk, secondary.pk, {}, proposal['version'], '合成链接合并')
        response = self.browser('alice').get(path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'/works/{self.work.pk}/')
        Work.objects.filter(pk=self.work.pk).update(hidden_at=timezone.now())
        self.assertEqual(self.browser('alice').get(path).status_code, 404)


    def test_synced_reason_gate_requires_own_recommendation_in_html_and_api(self):
        self.recommend('alice')
        bob = self.browser('bob')
        edit = f'/recommend/?work={self.work.pk}&edit=1'
        api = f'/api/v1/public/works/{self.work.pk}/reason/'
        self.assertEqual(bob.get(edit).status_code, 409)
        self.assertNotContains(bob.get('/'), f'href="{edit.replace("&", "&amp;")}"')
        self.assertEqual(self.post(bob, api, {'reason':'我的理由'}).status_code, 409)
        self.recommend('bob')
        self.assertEqual(bob.get(edit).status_code, 200)
        self.assertEqual(self.post(bob, api, {'reason':'我的理由'}).status_code, 200)
        self.relation('bob', 'recommend', False)
        self.assertEqual(bob.get(edit).status_code, 409)
        self.assertEqual(self.post(bob, api, {'reason':'修改理由'}).status_code, 409)

    def test_synced_feedback_is_private_and_follows_account_across_sessions(self):
        from booklist.models import Feedback
        guest = self.browser()
        response = guest.get('/feedback/?from=/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/account/login/'))
        payload = {'category':'metadata', 'message':'同步版反馈合成测试', 'page_url':'/'}
        self.assertEqual(self.post(guest, '/api/v1/feedback/', payload).status_code, 401)
        alice = self.browser('alice')
        self.assertEqual(self.post(alice, '/api/v1/feedback/', payload).status_code, 200)
        self.assertEqual(Feedback.objects.get().anonymous_client_id, self.readers['alice'].principal_id)
        # A genuinely separate session must see the same history.
        token, session = accounts.session(self.readers['alice'])
        other_device = self.browser()
        other_device.cookies[settings.ACCOUNT_COOKIE] = token
        self.assertContains(other_device.get('/feedback/'), payload['message'])
        self.assertNotContains(self.browser('bob').get('/feedback/'), payload['message'])
        self.assertNotContains(alice.get('/feedback/'), '当前匿名身份')
        Account.objects.filter(pk=self.readers['alice'].pk).update(is_active=False)
        self.assertEqual(self.post(alice, '/api/v1/feedback/', payload).status_code, 401)

    def test_synced_shared_shell_and_mobile_assets_keep_account_navigation(self):
        response = self.browser('alice').get('/')
        for fragment in ('logo.png', 'feedback.css', 'first-visit.mjs', '公共书单', 'shelf-recommend',
                         'account-nav', '消息', '青禾', '反馈', 'shelf-controls'):
            self.assertContains(response, fragment)
        self.assertNotContains(response, 'public-period.css')
        self.assertNotContains(response, '@alice')


@override_settings(REQUIRE_ACCOUNT=True)
class ConcurrentAccountTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Same isolated-test cleanup required by the existing append-only audit triggers.
        from django.db import connection
        with connection.cursor() as cursor:
            triggers = cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name, _ in triggers:
                cursor.execute('DROP TRIGGER "'+name.replace('"', '""')+'"')
        try:
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _, sql in triggers:
                    cursor.execute(sql)

    def test_concurrent_first_likes_commit_one_event_and_notification(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        _, first = accounts.register('first', '首荐', PASSWORD)
        _, second = accounts.register('second', '点赞者', PASSWORD)
        work = Work.objects.create(original_submitted_title='并发合成作品', public_added_at=timezone.now())
        books.set_relation(work, first.account.principal, 'recommend', True)
        barrier = Barrier(2)
        def write(key):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return services.idempotent(second.account.principal, 'concurrent.like', key,
                    {'enabled': True}, lambda: (books.set_relation(work, second.account.principal, 'like', True) or {'status': 'saved'}))
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, str(uuid.uuid4())) for _ in range(2)]
            self.assertEqual([future.result(timeout=20) for future in futures], [{'status': 'saved'}]*2)
        self.assertEqual(PublicLike.objects.count(), 1)
        self.assertEqual(WorkLikeEvent.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
