import sys
from pathlib import Path
import unittest
import tempfile
import warnings
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.wenku8.session import Client, LOGIN, allowed_url, login_form, SourceStop, TransientNetworkError


class SessionTests(unittest.TestCase):
    def test_only_recognized_transient_transport_errors_can_defer_to_next_day(self):
        for cause, expected, count in [('TimeoutError', TransientNetworkError, 3),
                                       ('SSLCertVerificationError', SourceStop, 1)]:
            with tempfile.TemporaryDirectory() as temporary:
                calls = []
                def fetch(*args):
                    calls.append(args[0])
                    return {'result': 'transport_failed', 'status': None, 'cause_class': cause}, None
                client = Client(Path(temporary)/'state', Path(temporary)/'credentials', sleeper=lambda _: None, fetch=fetch)
                with self.assertRaises(expected) as error:
                    client.request('https://www.wenku8.net/robots.txt')
                self.assertEqual(len(calls), count)
                if cause == 'SSLCertVerificationError':
                    self.assertNotIsInstance(error.exception, TransientNetworkError)

    def test_requests_are_confined_to_metadata_routes_over_https(self):
        for value in ['http://www.wenku8.net/login.php', 'https://example.com/login.php',
                      'https://www.wenku8.net/novel/1/1/1.htm',
                      'https://www.wenku8.net/modules/article/toplist.php?sort=allvisit',
                      'https://www.wenku8.net/modules/article/toplist.php?sort=lastupdate&page=2&page=3']:
            with self.assertRaises(SourceStop): allowed_url(value)
        self.assertEqual(allowed_url('https://www.wenku8.net/book/4151.htm'),
                         'https://www.wenku8.net/book/4151.htm')

    def form(self, action='https://www.wenku8.net/login.php?do=submit', extra=''):
        return ('<html><body><form method="post" action="'+action+'">'
                '<input name="username"><input name="password" type="password">'
                '<input name="action" type="hidden" value="login">'
                '<select name="usecookie"><option value="315360000">保存一年</option></select>'
                +extra+'</form></body></html>').encode('gb18030')

    def test_observed_year_choice_is_used_without_guessing_form_values(self):
        action, fields = login_form(self.form())
        self.assertEqual(fields['usecookie'], '315360000')
        self.assertEqual(fields['action'], 'login')

    def test_credentials_never_go_to_an_unexpected_form_action(self):
        with self.assertRaises(SourceStop): login_form(self.form(action='https://example.com/login.php'))
        with self.assertRaises(SourceStop): login_form(self.form(action='http://www.wenku8.net/login.php?do=submit'))

    def test_captcha_stops_instead_of_being_bypassed(self):
        with self.assertRaisesRegex(SourceStop, 'captcha'):
            login_form(self.form(extra='<input name="checkcode">'))

    def test_ambiguous_login_submission_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as temporary:
            credentials = Path(temporary)/'credentials.json'
            credentials.write_text('{"username":"fixture","password":"fixture"}', encoding='utf-8')
            client = Client(Path(temporary)/'state', credentials, sleeper=lambda _: None)
            calls = []
            def request(url, **kwargs):
                calls.append((url, kwargs.get('retry')))
                if url == LOGIN:
                    return {'media_type': 'text/html'}, self.form()
                raise SourceStop('source_transport_failed_after_bounded_retries')
            client.request = request
            client.login()
            self.assertEqual(calls, [(LOGIN, None), (LOGIN+'?do=submit', False)])
            self.assertTrue(client.cookies_path.is_file())

    def test_access_restriction_is_not_treated_as_an_ambiguous_login(self):
        with tempfile.TemporaryDirectory() as temporary:
            credentials = Path(temporary)/'credentials.json'
            credentials.write_text('{"username":"fixture","password":"fixture"}', encoding='utf-8')
            client = Client(Path(temporary)/'state', credentials, sleeper=lambda _: None)
            def request(url, **kwargs):
                if url == LOGIN:
                    return {'media_type': 'text/html'}, self.form()
                raise SourceStop('source_access_restricted')
            client.request = request
            with self.assertRaisesRegex(SourceStop, 'source_access_restricted'):
                client.login()

    def test_damaged_cookie_file_never_echoes_private_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'cookies.txt').write_text('# Netscape HTTP Cookie File\nprivate-cookie-fixture\n', encoding='utf-8')
            with warnings.catch_warnings(record=True) as emitted:
                with self.assertRaisesRegex(SourceStop, '^source_cookie_file_requires_review$'):
                    Client(directory, directory/'credentials.json')
            self.assertEqual(emitted, [])


if __name__ == '__main__': unittest.main()
