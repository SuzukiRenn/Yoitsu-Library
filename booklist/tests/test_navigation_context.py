from datetime import timedelta
from urllib.parse import urlencode,parse_qs,urlsplit
from django.conf import settings
from django.test import TestCase,override_settings
from django.utils import timezone
from booklist import accounts,services,activities,public_booklist as books
from booklist.models import Activity,Work,Notification,WorkLikeEvent
from booklist.navigation import safe_return


@override_settings(REQUIRE_ACCOUNT=True)
class NavigationContextTests(TestCase):
    def setUp(self):
        self.token,self.session=accounts.register('navigation_reader','读者','Navigation-Password!74381')
        self.person=self.session.account.principal
        self.work=Work.objects.create(original_submitted_title='返回书籍')
        books.set_relation(self.work,self.person,'recommend',True)

    def logged_in(self):self.client.cookies[settings.ACCOUNT_COOKIE]=self.token

    def test_safe_frontend_destinations_and_untrusted_fallbacks(self):
        value='/works/1/?'+urlencode({'from':'/?q=小说&page=3','browse':'abc'})+'#reasons'
        self.assertEqual(safe_return(value),value)
        for bad in ('https://evil.test/','//evil.test/','/\\evil.test/','/%5cevil.test/','/api/v1/identity/','/manage/','/random-book/','/notifications/1/work/','/account/login/','/?q=%0d%0aLocation:evil','/../api/v1/csrf/'):
            self.assertEqual(safe_return(bad), '/',bad)

    def test_top_login_and_notifications_preserve_page_filters(self):
        source='/?'+urlencode({'q':'返回','page_size':20,'sort':'likes','tag_mode':'any'})
        page=self.client.get(source)
        self.assertEqual(parse_qs(urlsplit(page.context['login_url']).query)['next'],[source])
        self.logged_in();page=self.client.get(source)
        self.assertEqual(parse_qs(urlsplit(page.context['notification_url']).query)['from'],[source])
        inbox=self.client.get('/notifications/?'+urlencode({'from':source}))
        self.assertContains(inbox,'data-context-return')
        self.assertEqual(inbox.context['return_url'],source)
        self.assertEqual(self.client.get('/notifications/').context['return_url'],'/')

    def test_reason_editor_returns_to_detail_with_full_shelf_chain(self):
        self.logged_in()
        source=f'/works/{self.work.pk}/?'+urlencode({'from':'/?q=返回&sort=likes&page=3','browse':'84623a2c-922f-482f-8dd5-2e6d67873c02'})+'#reasons'
        path=f'/recommend/?work={self.work.pk}&edit=1&'+urlencode({'from':source})
        self.assertEqual(self.client.get(path).context['success_url'],source)
        self.assertEqual(self.client.get(path.replace(urlencode({'from':source}),urlencode({'from':'//evil.test/'}))).context['success_url'],'/')
        self.client.cookies.clear()
        login=self.client.get(path)
        self.assertEqual(parse_qs(urlsplit(login.url).query)['next'],[path])

    def test_message_work_can_return_to_inbox_then_original_source(self):
        self.logged_in()
        event=WorkLikeEvent.objects.create(work=self.work,actor=self.session.account,scope_key='public')
        note=Notification.objects.create(event=event,recipient=self.session.account,work_title='返回书籍')
        inbox='/notifications/?'+urlencode({'from':'/?q=返回&page=2'})
        response=self.client.get(f'/notifications/{note.pk}/work/?'+urlencode({'from':inbox}))
        self.assertEqual(parse_qs(urlsplit(response.url).query)['from'],[inbox])
        detail=self.client.get(response.url)
        self.assertEqual(detail.context['return_url'],inbox)
        self.assertIsNone(detail.context['work_sequence'])
        note.refresh_from_db();self.assertIsNone(note.read_at)
        self.assertEqual(books.recommendations().count(),1)

    @override_settings(QQ_EMAIL_LOGIN=True)
    def test_legacy_login_to_binding_retains_next(self):
        self.logged_in()
        target=f'/works/{self.work.pk}/#reasons'
        redirect=self.client.get('/account/legacy-login/?'+urlencode({'next':target}))
        self.assertEqual(parse_qs(urlsplit(redirect.url).query)['next'],[target])
