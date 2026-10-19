import json
import os
import runpy
from unittest.mock import patch

from django.conf import settings
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from booklist.proxy import TrustedProxyMiddleware
from config.proxy import trusted_proxy_ips


class ProxyConfigurationTests(SimpleTestCase):
    def test_exact_addresses_only_and_shared_gunicorn_configuration(self):
        self.assertEqual(trusted_proxy_ips('172.30.29.2, ::ffff:172.30.29.2, ::1'), ('172.30.29.2', '::1'))
        self.assertEqual(trusted_proxy_ips(''), ())
        for value in ('*', '172.30.29.0/28', 'proxy', '127.0.0.1,', 'fe80::1%eth0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trusted_proxy_ips(value)
        with patch.dict(os.environ, {'BOOKLIST_TRUSTED_PROXY_IPS': '172.30.29.2'}):
            config = runpy.run_path(str(settings.BASE_DIR / 'deploy/gunicorn.conf.py'))
        self.assertEqual(set(config['forwarded_allow_ips'].split(',')), {'127.0.0.1', '::1', '172.30.29.2'})
        with patch.dict(os.environ, {'BOOKLIST_TRUSTED_PROXY_IPS': '*'}), self.assertRaises(ValueError):
            runpy.run_path(str(settings.BASE_DIR / 'deploy/gunicorn.conf.py'))

    @override_settings(TRUSTED_PROXY_IPS=('172.30.29.2',))
    def test_forwarded_identity_is_single_hop_and_headers_are_removed(self):
        factory = RequestFactory()
        def echo(request):
            return JsonResponse({'ip': request.META['REMOTE_ADDR'], 'secure': request.is_secure(),
                                 'forwarded': any(k in request.META for k in (
                                     'HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR',
                                     'HTTP_X_FORWARDED_HOST', 'HTTP_X_REAL_IP', 'HTTP_X_FORWARDED_PROTO'))})
        middleware = TrustedProxyMiddleware(echo)
        request = factory.get('/', REMOTE_ADDR='172.30.29.2', HTTP_X_FORWARDED_FOR='::ffff:203.0.113.8',
                              HTTP_FORWARDED='for=attacker', HTTP_X_FORWARDED_HOST='attacker.example',
                              HTTP_X_REAL_IP='attacker')
        self.assertEqual(json.loads(middleware(request).content), {'ip': '203.0.113.8', 'secure': False, 'forwarded': False})
        # A direct client cannot choose its rate-limit identity or TLS scheme.
        request = factory.get('/', REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR='203.0.113.8',
                              HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(json.loads(middleware(request).content), {'ip': '203.0.113.9', 'secure': False, 'forwarded': False})
        for value in ('203.0.113.8, 203.0.113.9', '', 'unknown', 'fe80::1%eth0'):
            with self.subTest(value=value):
                response = middleware(factory.get('/', REMOTE_ADDR='172.30.29.2', HTTP_X_FORWARDED_FOR=value))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(value.encode() if value else b'unknown', response.content)
        self.assertEqual(middleware(factory.get('/', REMOTE_ADDR='172.30.29.2')).status_code, 200)

    def test_legal_footer_only_shows_configured_number(self):
        request = RequestFactory().get('/')
        for template in ('booklist/base.html', 'booklist/admin/base.html', 'booklist/admin/login.html'):
            with self.subTest(template=template):
                with override_settings(ICP_RECORD=''):
                    self.assertNotIn('beian.miit.gov.cn', render_to_string(template, request=request))
                with override_settings(ICP_RECORD='黑ICP备12345678号-1'):
                    html = render_to_string(template, request=request)
                    self.assertIn('https://beian.miit.gov.cn/', html)
                    self.assertIn('黑ICP备12345678号-1', html)


@override_settings(TRUSTED_PROXY_IPS=('172.30.29.2',), ALLOWED_HOSTS=['books.example'],
                   SECURE_SSL_REDIRECT=True, CSRF_COOKIE_SECURE=True, COOKIE_SECURE=True)
class ProxyRequestTests(TestCase):
    def test_https_csrf_and_secure_cookie_through_trusted_proxy(self):
        client = Client(enforce_csrf_checks=True)
        headers = {'HTTP_HOST': 'books.example', 'REMOTE_ADDR': '172.30.29.2',
                   'HTTP_X_FORWARDED_FOR': '203.0.113.8'}
        response = client.get('/api/v1/csrf/', secure=True, **headers)
        self.assertEqual(response.status_code, 200)  # No redirect loop after Gunicorn sets HTTPS.
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]['secure'])
        token = response.json()['csrf_token']
        response = client.post('/api/v1/identity/', '{}', content_type='application/json', secure=True,
                               HTTP_ORIGIN='https://books.example', HTTP_X_CSRFTOKEN=token, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies[settings.ANONYMOUS_COOKIE]['secure'])
        response = client.post('/api/v1/identity/', '{}', content_type='application/json', secure=True,
                               HTTP_ORIGIN='https://attacker.example', HTTP_X_CSRFTOKEN=token, **headers)
        self.assertEqual(response.status_code, 403)
        response = client.get('/api/v1/csrf/', HTTP_X_FORWARDED_PROTO='https', **headers)
        self.assertEqual(response.status_code, 301)  # Header alone cannot bypass secure redirect.

    def test_search_rate_limit_is_per_real_client_not_proxy(self):
        headers = {'HTTP_HOST': 'books.example', 'REMOTE_ADDR': '172.30.29.2'}
        for _ in range(60):
            response = self.client.get('/api/v1/catalog/search/?q=test', secure=True,
                                       HTTP_X_FORWARDED_FOR='203.0.113.8', **headers)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/v1/catalog/search/?q=test', secure=True,
                                         HTTP_X_FORWARDED_FOR='203.0.113.8', **headers).status_code, 429)
        self.assertEqual(self.client.get('/api/v1/catalog/search/?q=test', secure=True,
                                         HTTP_X_FORWARDED_FOR='203.0.113.9', **headers).status_code, 200)
