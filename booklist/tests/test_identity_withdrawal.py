import json
import uuid
from datetime import timedelta
from django.conf import settings
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from booklist import accounts,activities,services,public_booklist as books
from booklist.models import AccountSession,Work,Activity


@override_settings(REQUIRE_ACCOUNT=True)
class IdentityWithdrawalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.token,cls.session=accounts.register('withdrawalice','读者甲','Identity-Withdraw!Password-49')
        cls.other_token,cls.other_session=accounts.register('withdrawbob','读者乙','Identity-Withdraw!Password-49')
        cls.admin=services.initialize_admin('withdrawowner','Identity-Withdraw!Admin-49')

    def setUp(self):
        self.actor=self.session.account.principal
        self.work=Work.objects.create(wenku8_book_id=123456,identity_status='matched',wenku8_title='账户撤回验收')
        books.submit(self.actor,{'work_id':self.work.pk,'title':self.work.display_title,'reason':'账户理由','confirmed_unmatched':False})
        self.url=f'/api/v1/public/works/{self.work.pk}/recommend/'
        self.client.cookies[settings.ACCOUNT_COOKIE]=self.token

    def post(self,browser=None,payload=None,url=None):
        return (browser or self.client).post(url or self.url,json.dumps(payload or {'enabled':False}),content_type='application/json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

    def prompt(self,url=None):
        response=self.post(url=url)
        self.assertEqual(response.status_code,409,response.content)
        return response.json()['error']['confirmation']['token']

    def test_same_account_second_device_can_confirm_without_losing_attribution(self):
        token=self.prompt()
        session_token,_=accounts.login('withdrawalice','Identity-Withdraw!Password-49')
        second=Client();second.cookies[settings.ACCOUNT_COOKIE]=session_token
        response=self.post(second,{'enabled':False,'confirmation':token})
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(books.recommendations().filter(work=self.work).exists())
        self.assertEqual(self.session.account.display_name,'读者甲')

    def test_session_revoked_after_prompt_cannot_confirm(self):
        token=self.prompt()
        AccountSession.objects.filter(account=self.session.account).delete()
        response=self.post(payload={'enabled':False,'confirmation':token})
        self.assertEqual(response.status_code,401,response.content)
        self.assertTrue(books.recommendations().filter(work=self.work).exists())

    def test_confirmation_cannot_be_transferred_to_another_account_or_work(self):
        token=self.prompt()
        other=Work.objects.create(wenku8_book_id=123457,identity_status='matched',wenku8_title='另一账户作品')
        books.enroll(other);books.set_relation(other,self.other_session.account.principal,'recommend',True)
        browser=Client();browser.cookies[settings.ACCOUNT_COOKIE]=self.other_token
        response=self.post(browser,{'enabled':False,'confirmation':token},f'/api/v1/public/works/{other.pk}/recommend/')
        self.assertEqual(response.status_code,409,response.content)
        self.assertTrue(books.recommendations().filter(work=other).exists())

    def test_activity_confirmation_keeps_public_account_recommendation(self):
        now=timezone.now()
        period=Activity.objects.create(title='账户主题',created_by=self.admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        activities.submit(period.pk,self.actor,{'work_id':self.work.pk,'title':self.work.display_title,'reason':'主题理由','confirmed_unmatched':False,'copy_public_reason':False})
        url=f'/api/v1/activities/{period.pk}/works/{self.work.pk}/recommend/'
        token=self.prompt(url)
        response=self.post(payload={'enabled':False,'confirmation':token},url=url)
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(activities.recs(period.pk).filter(work=self.work).exists())
        self.assertTrue(books.recommendations().filter(work=self.work,anonymous_client=self.actor).exists())
