from django.test import TestCase, override_settings
from booklist.models import AnonymousClient


class LaunchInformationTests(TestCase):
    def test_guest_can_read_all_information_without_creating_identity(self):
        for path, title in [('/privacy/', '隐私说明'), ('/guidelines/', '使用与内容规则'), ('/contact/', '联系与投诉')]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, title)
            if path != '/guidelines/':
                self.assertContains(response, 'kuroneko0217@outlook.com')
            self.assertFalse(response.cookies)
            self.assertIn("default-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(AnonymousClient.objects.count(), 0)

    @override_settings(ICP_RECORD='')
    def test_pending_record_is_plain_placeholder_not_fake_number(self):
        response = self.client.get('/contact/')
        self.assertContains(response, '备案信息待补充')
        self.assertNotContains(response, 'ICP备000')
        self.assertNotContains(response, 'href="https://beian.miit.gov.cn/"')

    @override_settings(ICP_RECORD='京ICP备12345678号-1')
    def test_real_configured_record_is_linked(self):
        response = self.client.get('/contact/')
        self.assertContains(response, '京ICP备12345678号-1')
        self.assertContains(response, 'href="https://beian.miit.gov.cn/"')
        self.assertNotContains(response, '备案信息待补充')

    def test_footer_navigation_present(self):
        response = self.client.get('/contact/')
        for path in ['/privacy/', '/guidelines/', '/contact/']:
            self.assertContains(response, 'href="' + path + '"')

    def test_post_to_information_is_rejected(self):
        for path in ['/privacy/', '/guidelines/', '/contact/']:
            self.assertEqual(self.client.post(path).status_code, 405)

    def test_privacy_describes_the_actual_edition(self):
        from django.conf import settings
        response = self.client.get('/privacy/')
        if getattr(settings, 'ACCOUNT_COOKIE', ''):
            self.assertContains(response, '目前没有自助注销按钮')
            self.assertContains(response, '最长30天')
        else:
            self.assertContains(response, '最长365天')
            self.assertContains(response, '无法自动找回旧身份')
