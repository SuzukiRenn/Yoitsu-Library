import json
import re
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from booklist import accounts, qq_login, services
from booklist.models import Account, AccountSession, EmailChallenge, Work, PublicRecommendation


@override_settings(QQ_EMAIL_LOGIN=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='sender@qq.com', REQUIRE_ACCOUNT=True)
class QQLoginTests(TestCase):
    def issue(self,email='123456@qq.com',purpose='login',account=None):
        result=qq_login.request_code(email,purpose,'192.0.2.10',account)
        code=re.search(r'\b[0-9]{6}\b',mail.outbox[-1].body).group()
        return result,code

    def verify(self,result,code,email='123456@qq.com',purpose='login',account=None):
        return qq_login.verify(email,result['challenge'],code,purpose,account)

    def test_new_email_creates_passwordless_account_and_one_time_session(self):
        result,code=self.issue(' 123456@QQ.COM ')
        stored=EmailChallenge.objects.get()
        self.assertNotIn(code,json.dumps(result));self.assertNotEqual(stored.code_hash,code)
        token,session=self.verify(result,code)
        self.assertEqual(session.account.qq_email,'123456@qq.com')
        self.assertTrue(session.account.password_hash.startswith('!'))
        self.assertTrue(session.account.email_verified_at)
        with self.assertRaises(services.DomainError):self.verify(result,code)
        self.assertEqual(Account.objects.count(),1)

    def test_mail_preview_keeps_six_digit_code_separate_when_line_breaks_are_removed(self):
        with patch('booklist.qq_login.secrets.randbelow', return_value=7):
            result, code = self.issue()
        self.assertEqual(code, '000007')
        preview = re.sub(r'\s+', '', mail.outbox[-1].body)
        self.assertIn('【000007】', preview)
        self.assertEqual(re.findall(r'[0-9]+', preview), ['000007'])
        self.assertIn('有效期：五分钟', preview)
        self.verify(result, code)

    def test_existing_email_login_does_not_create_or_rewrite_profile(self):
        result,code=self.issue();_,session=self.verify(result,code)
        account=session.account;account.display_name='原昵称';account.save()
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        result,code=self.issue();_,again=self.verify(result,code)
        self.assertEqual(again.account.pk,account.pk);self.assertEqual(again.account.display_name,'原昵称')
        self.assertEqual(Account.objects.count(),1)

    def test_rejects_other_domains_and_spoofed_or_invalid_addresses(self):
        for email in ['a@foxmail.com','a@qq.com.evil','a@@qq.com','a\n@qq.com','用户名@qq.com']:
            with self.subTest(email=email),self.assertRaises(services.DomainError):self.issue(email)
        self.assertFalse(EmailChallenge.objects.exists())

    def test_cooldown_and_resend_invalidate_prior_code(self):
        result,code=self.issue()
        with self.assertRaises(services.DomainError) as caught:self.issue()
        self.assertEqual(caught.exception.status,429)
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        newer,newcode=self.issue()
        with self.assertRaises(services.DomainError):self.verify(result,code)
        self.verify(newer,newcode)

    def test_five_wrong_attempts_commit_and_lock_out_correct_code(self):
        result,code=self.issue();wrong='000000' if code!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(services.DomainError):self.verify(result,wrong)
        self.assertEqual(EmailChallenge.objects.get().attempts,5)
        with self.assertRaises(services.DomainError):self.verify(result,code)
        self.assertFalse(Account.objects.exists())

    def test_expired_or_mismatched_email_or_token_rejected(self):
        result,code=self.issue()
        with self.assertRaises(services.DomainError):self.verify(result,code,email='777777@qq.com')
        EmailChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(services.DomainError):self.verify(result,code)

    def test_provider_failure_is_generic_and_cannot_authenticate(self):
        with patch('booklist.qq_login.send_mail',side_effect=RuntimeError('secret SMTP diagnostic')):
            with self.assertRaises(services.DomainError) as caught:self.issue()
        self.assertEqual(caught.exception.status,503)
        self.assertNotIn('secret',caught.exception.message)
        self.assertIsNotNone(EmailChallenge.objects.get().consumed_at)
        self.assertFalse(Account.objects.exists())

    def test_disabled_account_stays_disabled(self):
        result,code=self.issue();_,session=self.verify(result,code)
        Account.objects.filter(pk=session.account.pk).update(is_active=False)
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        result,code=self.issue()
        with self.assertRaises(services.DomainError) as caught:self.verify(result,code)
        self.assertEqual(caught.exception.status,403)
        self.assertEqual(Account.objects.count(),1)

    def test_binding_keeps_existing_principal_and_recommendations_and_revokes_old_sessions(self):
        _,old=accounts.register('legacy','原读者','Old-Reader!Password-2026')
        account=old.account;principal=account.principal_id
        work=Work.objects.create(original_submitted_title='保留记录')
        record=PublicRecommendation.objects.create(work=work,anonymous_client=account.principal)
        result,code=self.issue(purpose='bind',account=account)
        with self.assertRaises(services.DomainError):self.verify(result,code)
        token,new=self.verify(result,code,purpose='bind',account=account)
        self.assertEqual(new.account.pk,account.pk);self.assertEqual(new.account.principal_id,principal)
        self.assertTrue(PublicRecommendation.objects.filter(pk=record.pk,anonymous_client_id=principal).exists())
        self.assertFalse(AccountSession.objects.filter(pk=old.pk).exists())
        self.assertEqual(AccountSession.objects.filter(account=account).count(),1)

    def test_binding_cannot_take_another_accounts_email_or_rebind(self):
        result,code=self.issue();_,owner=self.verify(result,code)
        _,legacy=accounts.register('legacy','原读者','Old-Reader!Password-2026')
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        result,code=self.issue(purpose='bind',account=legacy.account)
        with self.assertRaises(services.DomainError):self.verify(result,code,purpose='bind',account=legacy.account)
        legacy.account.refresh_from_db();self.assertIsNone(legacy.account.qq_email)
        with self.assertRaises(services.DomainError):self.issue('888888@qq.com','bind',owner.account)

    @override_settings(QQ_CODE_IP_HOURLY_LIMIT=30)
    def test_rate_limits_bound_cost_and_cleanup_removes_expired_codes(self):
        self.issue()
        for _ in range(29):services.rate_limit('qq_code_ip','192.0.2.10',30,3600)
        with self.assertRaises(services.DomainError):self.issue('777777@qq.com')
        EmailChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        call_command('cleanup_security',verbosity=0)
        self.assertFalse(EmailChallenge.objects.exists())

    def test_pages_and_legacy_migration_gate_and_private_email(self):
        client=Client()
        self.assertContains(client.get('/account/login/'),'QQ 邮箱登录')
        self.assertNotContains(client.get('/account/login/'),'name="password"')
        self.assertEqual(client.get('/account/register/').status_code,302)
        self.assertContains(client.get('/privacy/'),'QQ 邮箱')
        self.assertContains(client.get('/account/legacy-login/'),'name="password"')
        token,old=accounts.register('legacy','原读者','Old-Reader!Password-2026')
        client.cookies[settings.ACCOUNT_COOKIE]=token
        self.assertEqual(client.get('/api/v1/notifications/unread/').status_code,403)
        self.assertContains(client.get('/account/bind/'),'验证并绑定')
        result,code=self.issue(purpose='bind',account=old.account)
        response=client.post('/api/v1/account/email/bind/',json.dumps({'email':'123456@qq.com','challenge':result['challenge'],'code':code}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('qq_email',response.json()['account'])
        self.assertContains(client.get('/account/settings/'),'123456@qq.com')
        self.assertNotContains(client.get('/'),'123456@qq.com')
        self.assertEqual(client.get('/api/v1/notifications/unread/').status_code,200)
        response=client.post('/api/v1/account/login/',json.dumps({'username':'legacy','password':'Old-Reader!Password-2026'}),content_type='application/json')
        self.assertEqual(response.status_code,409)

    def test_csrf_and_disabled_feature_fail_closed_and_admin_remains_password_based(self):
        client=Client(enforce_csrf_checks=True)
        response=client.post('/api/v1/account/email/code/',json.dumps({'email':'123456@qq.com','purpose':'login'}),content_type='application/json')
        self.assertEqual(response.status_code,403)
        with override_settings(QQ_EMAIL_LOGIN=False):
            with self.assertRaises(services.DomainError):self.issue()
        self.assertContains(Client().get('/manage/login/'),'name="password"')

    def test_binding_rechecks_account_version_after_password_reset(self):
        _,old=accounts.register('legacy','旧读者','Old-Reader!Password-2026')
        result,code=self.issue(purpose='bind',account=old.account)
        Account.objects.filter(pk=old.account.pk).update(session_version=old.account.session_version+1)
        with self.assertRaises(services.DomainError):self.verify(result,code,purpose='bind',account=old.account)
        old.account.refresh_from_db();self.assertIsNone(old.account.qq_email)

    def test_cooldown_retries_do_not_burn_delivery_quota(self):
        self.issue()
        for _ in range(7):
            with self.assertRaises(services.DomainError):self.issue()
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        self.issue()
        self.assertEqual(len(mail.outbox),2)


    def test_shared_ip_allows_more_than_old_limit_and_mailbox_reuses_old_counter(self):
        for _ in range(30):services.rate_limit('qq_code_ip','192.0.2.10',1000,3600)
        for _ in range(8):services.rate_limit('qq_code_email','123456@qq.com',30,86400)
        self.issue()
        self.assertEqual(len(mail.outbox),1)

    @override_settings(QQ_CODE_EMAIL_DAILY_LIMIT=1)
    def test_email_daily_limit_returns_real_reset_time_and_header(self):
        client=Client()
        with patch('booklist.services.time.time',return_value=1700000100.0):
            self.issue()
            EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
            response=client.post('/api/v1/account/email/code/',json.dumps({'email':'123456@qq.com','purpose':'login'}),content_type='application/json',REMOTE_ADDR='192.0.2.10')
        self.assertEqual(response.status_code,429)
        error=response.json()['error']
        self.assertEqual(error['code'],'qq_code_email_limit')
        self.assertEqual(error['retry_after'],(1700000100//86400+1)*86400-1700000100)
        self.assertEqual(int(response['Retry-After']),error['retry_after'])
        self.assertIn('北京时间',error['message'])
        self.assertTrue(error['retry_at'].endswith('08:00:00+08:00'))

    @override_settings(QQ_CODE_IP_HOURLY_LIMIT=1)
    def test_ip_limit_has_distinct_message_and_mailbox_cooldown_is_remaining_time(self):
        client=Client()
        self.issue()
        response=client.post('/api/v1/account/email/code/',json.dumps({'email':'another@qq.com','purpose':'login'}),content_type='application/json',REMOTE_ADDR='192.0.2.10')
        self.assertEqual(response.json()['error']['code'],'qq_code_ip_limit')
        with override_settings(QQ_CODE_IP_HOURLY_LIMIT=1000):
            EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=30))
            with self.assertRaises(services.DomainError) as caught:self.issue()
        self.assertEqual(caught.exception.code,'code_cooldown')
        self.assertTrue(29<=caught.exception.retry_after<=31)

    @override_settings(QQ_CODE_GLOBAL_DAILY_LIMIT=1)
    def test_global_quota_and_verification_limit_are_specific_and_do_not_send(self):
        self.issue()
        with self.assertRaises(services.DomainError) as caught:self.issue('777777@qq.com')
        self.assertEqual(caught.exception.code,'qq_code_global_limit')
        with override_settings(QQ_VERIFY_IP_LIMIT=1):
            services.rate_limit('qq_verify_ip','127.0.0.1',1,900)
            response=Client().post('/api/v1/account/email/login/',json.dumps({'email':'123456@qq.com','challenge':'a'*43,'code':'000000'}),content_type='application/json')
        self.assertEqual(response.json()['error']['code'],'qq_verify_ip_limit')
        self.assertEqual(len(mail.outbox),1)
