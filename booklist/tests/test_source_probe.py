"""Transport substitutes only; no live website calls."""
from email.message import Message
import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, build_opener
from scripts import probe_wenku8 as source


class Response(io.BytesIO):
    def __init__(self, raw, media='text/plain', encoding=None):
        super().__init__(raw)
        self.status = 200
        self.headers = Message()
        self.headers['Content-Type'] = media
        if encoding:
            self.headers['Content-Encoding'] = encoding


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SourceProbeTests(unittest.TestCase):
    def run_probe(self, *responses):
        opener = Opener(responses)
        sleeps = []
        return source.probe('local', opener, sleeps.append), opener, sleeps

    def test_allowed_is_bounded_and_never_claims_valid_metadata(self):
        result, opener, sleeps = self.run_probe(Response(b'User-agent: *\nDisallow:\n'), Response(b'<html>login</html>', 'text/html'))
        self.assertEqual(result['outcome'], 'detail_http_received_metadata_unverified')
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(sleeps, [10])
        for request, timeout in opener.calls:
            self.assertEqual(timeout, 15)
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('Authorization', request.headers)
        self.assertNotIn('<html>', json.dumps(result))

    def test_robots_denial_stops_before_detail(self):
        result, opener, sleeps = self.run_probe(Response(b'User-agent: *\nDisallow: /book/\n'))
        self.assertEqual(result['stop_reason'], 'robots_disallows_detail')
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(sleeps, [])

    def test_failed_robots_never_retries_or_leaks_proxy(self):
        result, opener, _ = self.run_probe(URLError('https://secret:password@proxy.invalid/'))
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result['stop_reason'], 'robots_fetch_failed')
        self.assertNotIn('password', json.dumps(result))

    def test_http_denial_and_redirect_stop(self):
        for status in (301, 302, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                result, opener, _ = self.run_probe(HTTPError(source.BASE, status, 'private reason', {}, None))
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(result['requests'][0]['status'], status)
                self.assertNotIn('private reason', json.dumps(result))

    def test_actual_redirect_handler_refuses_navigation(self):
        opener = build_opener(source.NoRedirect())
        handlers = [h for h in opener.handlers if isinstance(h, HTTPRedirectHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, '', {}, 'https://other.invalid'))
        self.assertFalse(any(isinstance(h, HTTPCookieProcessor) for h in opener.handlers))

    def test_oversize_robots_stops(self):
        result, opener, _ = self.run_probe(Response(b'x' * (source.ROBOTS_LIMIT + 2)))
        self.assertEqual(result['requests'][0]['result'], 'response_too_large')
        self.assertEqual(len(opener.calls), 1)

    def test_html_and_invalid_policy_stop(self):
        for raw, media in ((b'<html>challenge</html>', 'text/plain'), (b'User-agent: *', 'text/html'), (b'not a robots file', 'text/plain'), (b'\xff', 'text/plain')):
            with self.subTest(raw=raw):
                result, opener, _ = self.run_probe(Response(raw, media))
                self.assertEqual(result['stop_reason'], 'robots_unrecognized')
                self.assertEqual(len(opener.calls), 1)

    def test_crawl_delay_and_request_rate_are_honoured(self):
        result, _, sleeps = self.run_probe(Response(b'User-agent: *\nDisallow:\nCrawl-delay: 12\nRequest-rate: 1/20\n'), Response(b'x', 'text/html'))
        self.assertEqual(sleeps, [20])
        self.assertEqual(result['robots']['minimum_interval_seconds'], 20)

    def test_long_or_unsupported_timing_stops(self):
        for timing, reason in ((b'Crawl-delay: 61', 'robots_timing_requires_manual_schedule'), (b'Crawl-delay: 0.5', 'robots_unsupported_timing'), (b'Request-rate: invalid', 'robots_unsupported_timing')):
            result, opener, sleeps = self.run_probe(Response(b'User-agent: *\n' + timing))
            self.assertEqual(result['stop_reason'], reason)
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(sleeps, [])

    def test_compressed_response_not_decompressed_or_parsed(self):
        result, opener, _ = self.run_probe(Response(b'compressed', encoding='gzip'))
        self.assertEqual(result['requests'][0]['result'], 'unsupported_content_encoding')
        self.assertEqual(len(opener.calls), 1)

    def test_detail_failure_no_retry(self):
        result, opener, _ = self.run_probe(Response(b'User-agent: *'), URLError('no route'))
        self.assertEqual(result['stop_reason'], 'detail_fetch_failed')
        self.assertEqual(len(opener.calls), 2)

    def test_detail_oversize_and_wrong_type_stop(self):
        for response, reason in ((Response(b'x' * (source.DETAIL_LIMIT + 1), 'text/html'), 'detail_fetch_failed'), (Response(b'x', 'application/json'), 'detail_unrecognized_media_type')):
            result, opener, _ = self.run_probe(Response(b'User-agent: *'), response)
            self.assertEqual(result['stop_reason'], reason)
            self.assertEqual(len(opener.calls), 2)
