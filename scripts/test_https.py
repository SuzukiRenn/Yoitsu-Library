import json
import unittest

from prepare_https import render, validate


class PreparationTests(unittest.TestCase):
    def test_domain_cannot_inject_nginx_or_yaml_configuration(self):
        for domain in ('https://books.example', 'books.example/path', '*.example.com',
                       '127.0.0.1', 'books.example;return 200;', 'a..example', '-a.example',
                       'books.example\nserver', '书单.example'):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                render(domain, 'a' * 40)
        self.assertEqual(validate('BOOKS.EXAMPLE', 'a' * 40, '172.30.29.0/28', '172.30.29.2')[0], 'books.example')

    def test_bad_release_and_network_fail_before_writing(self):
        for release, subnet, proxy in (('main', '172.30.29.0/28', '172.30.29.2'),
                                       ('a'*40, '0.0.0.0/0', '172.30.29.2'),
                                       ('a'*40, '172.30.29.0/28', '172.30.29.1'),
                                       ('a'*40, '172.30.29.0/28', '172.30.29.15'),
                                       ('a'*40, '172.30.29.0/28', '172.30.28.2')):
            with self.subTest(subnet=subnet, proxy=proxy), self.assertRaises(ValueError):
                render('books.example', release, subnet, proxy)

    def test_generated_configuration_has_no_public_listener_or_certificate_action(self):
        files = render('books.example', 'a'*40)
        compose = json.loads(files['compose.https.json'])
        self.assertNotIn('ports', compose['services']['app'])
        for value in compose['services']['proxy']['ports']:
            self.assertTrue(value.startswith('127.0.0.1:'))
        self.assertTrue(compose['networks']['edge_backend']['internal'])
        self.assertEqual(compose['services']['certbot']['profiles'], ['certificates'])
        self.assertEqual(compose['services']['certbot']['command'], ['--version'])
        proxy_ip = compose['services']['proxy']['networks']['edge_backend']['ipv4_address']
        self.assertEqual(compose['services']['app']['environment']['BOOKLIST_TRUSTED_PROXY_IPS'], proxy_ip)
        self.assertNotIn('proxy_pass', files['nginx.bootstrap.conf'])
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', files['nginx.https.conf'])
        self.assertNotIn('$proxy_add_x_forwarded_for', files['nginx.https.conf'])
        self.assertIn('text/javascript js mjs;', files['nginx.https.conf'])
        self.assertFalse(json.loads(files['manifest.json'])['server_modified'])


if __name__ == '__main__':
    unittest.main()
