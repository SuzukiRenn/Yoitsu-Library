import importlib,json,re
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode,parse_qs,urlsplit
from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import Client,TestCase,override_settings
from django.utils import timezone
from booklist import accounts,qq_login,nickname_setup
from booklist.models import Account,AccountSession,EmailChallenge,Work,PublicRecommendation

@override_settings(QQ_EMAIL_LOGIN=True,REQUIRE_ACCOUNT=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='synthetic@qq.com')
class NicknameSetupTests(TestCase):
    def issue(self,email='123456@qq.com'):
        result=qq_login.request_code(email,'login','192.0.2.20')
        code=re.search(r'【([0-9]{6})】',mail.outbox[-1].body).group(1)
        return {'email':email,'challenge':result['challenge'],'code':code}

    def browser(self,token=None):
        c=Client(enforce_csrf_checks=True)
        if token:c.cookies[settings.ACCOUNT_COOKIE]=token
        return c

    def post(self,c,path,data):
        csrf=c.get('/api/v1/csrf/').json()['csrf_token']
        return c.post(path,json.dumps(data),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)

    def reader(self,email='123456@qq.com'):
        c=self.browser();response=self.post(c,'/api/v1/account/email/login/',self.issue(email))
        self.assertEqual(response.status_code,200,response.content)
        return c,Account.objects.get(qq_email=email),response

    def test_new_login_requires_nickname_and_retains_account_principal(self):
        c,account,response=self.reader()
        self.assertTrue(response.json()['nickname_required'])
        self.assertIsNone(account.nickname_confirmed_at)
        source='/rankings/?year=2025&category=bunko&shown=20'
        destination=c.get(source)
        self.assertEqual(destination.status_code,302)
        self.assertEqual(parse_qs(urlsplit(destination.url).query)['next'],[source])
        page=c.get(destination.url)
        self.assertContains(page,'data-nickname-setup')
        self.assertContains(page,'保存并继续')
        self.assertNotContains(page,'value="'+account.display_name+'"')
        principal=account.principal_id
        work=Work.objects.create(original_submitted_title='既有推荐')
        rec=PublicRecommendation.objects.create(work=work,anonymous_client_id=principal)
        self.assertEqual(self.post(c,'/api/v1/account/profile/',{'display_name':'花绿青'}).status_code,200)
        account.refresh_from_db()
        self.assertIsNotNone(account.nickname_confirmed_at)
        self.assertEqual(account.display_name,'花绿青')
        self.assertEqual(account.principal_id,principal)
        self.assertTrue(PublicRecommendation.objects.filter(pk=rec.pk,anonymous_client_id=principal).exists())
        self.assertEqual(c.get(source).status_code,200)

    def test_saved_once_survives_logout_relogin_and_other_device(self):
        c,account,_=self.reader()
        token,session=accounts.session(account);other=self.browser(token)
        self.assertEqual(other.get('/').status_code,302)
        self.post(c,'/api/v1/account/profile/',{'display_name':'读者abcdef'})
        self.assertEqual(other.get('/').status_code,200)
        self.assertFalse(other.get('/api/v1/account/me/').json()['nickname_required'])
        account.refresh_from_db();first=account.nickname_confirmed_at
        self.post(other,'/api/v1/account/profile/',{'display_name':'再次修改'})
        account.refresh_from_db();self.assertEqual(account.nickname_confirmed_at,first)
        self.assertEqual(self.post(c,'/api/v1/account/logout/',{}).status_code,200)
        EmailChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        response=self.post(c,'/api/v1/account/email/login/',self.issue())
        self.assertFalse(response.json()['nickname_required'])
        self.assertEqual(response.json()['account']['id'],account.pk)
        self.assertEqual(Account.objects.count(),1)
        self.assertRedirects(c.get('/account/nickname/?next='+urlencode({'x':'/'})), '/',fetch_redirect_response=False)

    def test_blank_invalid_and_spoofed_completion_never_finish_setup(self):
        c,account,_=self.reader()
        for value in ('','  ','\u200b','\n','x'*41):
            self.assertEqual(self.post(c,'/api/v1/account/profile/',{'display_name':value}).status_code,400)
            account.refresh_from_db();self.assertIsNone(account.nickname_confirmed_at)
        self.assertEqual(self.post(c,'/api/v1/account/profile/',{'display_name':'新昵称','nickname_confirmed_at':'now'}).status_code,400)
        self.assertEqual(c.post('/api/v1/account/profile/',json.dumps({'display_name':'新昵称'}),content_type='application/json').status_code,403)
        account.refresh_from_db();self.assertIsNone(account.nickname_confirmed_at)

    def test_pages_and_write_requests_cannot_skip_setup_but_logout_and_admin_are_available(self):
        c,account,_=self.reader()
        for path in ('/','/account/','/account/settings/','/notifications/','/recommend/','/activities/'):
            self.assertEqual(urlsplit(c.get(path).url).path,'/account/nickname/')
        work=Work.objects.create(original_submitted_title='禁止跳过')
        response=self.post(c,f'/api/v1/public/works/{work.pk}/recommend/',{'enabled':True})
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json()['error']['code'],'nickname_required')
        self.assertFalse(PublicRecommendation.objects.exists())
        for path in ('/manage/login/','/privacy/','/contact/','/api/v1/account/me/','/api/v1/activities/notices/'):
            self.assertEqual(c.get(path).status_code,200,path)
        self.assertEqual(self.post(c,'/api/v1/account/logout/',{}).status_code,200)
        self.assertEqual(c.get('/').status_code,200)

    def test_redirects_are_bounded_and_confirmed_users_never_see_form_again(self):
        c,account,_=self.reader()
        source='/activities/3/?q=book&tag=2#reasons'
        self.post(c,'/api/v1/account/profile/',{'display_name':'已填写'})
        for target,expected in [(source,source),('https://evil.test/','/'),('//evil.test/','/'),('/api/v1/account/logout/','/'),('/account/nickname/','/')]:
            response=c.get('/account/nickname/?'+urlencode({'next':target}))
            self.assertEqual(response.status_code,302)
            self.assertEqual(response.url,expected)
        guest=self.browser()
        response=guest.get('/account/nickname/?'+urlencode({'next':source}))
        self.assertEqual(urlsplit(response.url).path,'/account/login/')
        self.assertEqual(parse_qs(urlsplit(response.url).query)['next'],[source])

    def test_existing_password_registration_and_email_binding_keep_completed_name(self):
        token,session=accounts.register('legacy','读者abcdef','Nickname-Testing!2026')
        self.assertIsNotNone(session.account.nickname_confirmed_at)
        now=timezone.now()
        Account.objects.filter(pk=session.account.pk).update(qq_email='999999@qq.com',email_verified_at=now)
        c=self.browser(token)
        self.assertEqual(c.get('/').status_code,200)
        self.assertNotIn('nickname_confirmed_at',accounts.public(Account.objects.get(pk=session.account.pk)))
        self.assertNotIn('nickname_required',accounts.public(Account.objects.get(pk=session.account.pk)))

    def test_backfill_only_adds_confirmation_and_preserves_custom_names(self):
        c,generated,_=self.reader()
        _,custom,_=self.reader('234567@qq.com')
        Account.objects.filter(pk=custom.pk).update(display_name='原昵称')
        _,legacy=accounts.register('legacy','读者aabbcc','Nickname-Legacy!2026')
        Account.objects.filter(pk=legacy.account.pk).update(nickname_confirmed_at=None)
        fields=[f.attname for f in Account._meta.concrete_fields if f.name!='nickname_confirmed_at']
        before=list(Account.objects.order_by('pk').values(*fields))
        module=importlib.import_module('booklist.migrations.0022_account_nickname_confirmation')
        apps=SimpleNamespace(get_model=lambda app,model:Account)
        module.backfill(apps,SimpleNamespace(connection=connection))
        generated.refresh_from_db();custom.refresh_from_db();legacy.account.refresh_from_db()
        self.assertIsNone(generated.nickname_confirmed_at)
        self.assertIsNotNone(custom.nickname_confirmed_at)
        self.assertIsNotNone(legacy.account.nickname_confirmed_at)
        self.assertEqual(before,list(Account.objects.order_by('pk').values(*fields)))

    @override_settings(QQ_EMAIL_LOGIN=False)
    def test_disabled_email_feature_does_not_gate_other_login_modes(self):
        token,session=accounts.register('plain','旧昵称','Nickname-Plain!2026')
        Account.objects.filter(pk=session.account.pk).update(qq_email='999999@qq.com',nickname_confirmed_at=None)
        c=self.browser(token)
        self.assertEqual(c.get('/').status_code,200)
        self.assertFalse(nickname_setup.required(Account.objects.get(pk=session.account.pk)))
