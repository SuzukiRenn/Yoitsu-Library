"""Transport substitutes only; no live website calls."""
from email.message import Message
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
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

    def test_partial_body_timeout_preserves_progress_and_never_validates(self):
        class Partial(Response):
            def read1(self, size):
                if self.tell():
                    raise TimeoutError('private proxy address')
                return super().read1(3)
        calls = []
        result = source.probe('local', Opener([Response(b'User-agent: *'), Partial(b'abcdef', 'text/html')]),
                              lambda _: None, lambda raw: calls.append(raw))
        record = result['requests'][1]
        self.assertEqual(record['bytes_read'], 3)
        self.assertEqual(record['chunks_read'], 1)
        self.assertEqual(record['phase'], 'body')
        self.assertFalse(record['body_complete'])
        self.assertEqual(record['error_class'], 'TimeoutError')
        self.assertEqual(calls, [])
        self.assertNotIn('private proxy', json.dumps(result))

    def test_zero_body_timeout_is_distinct(self):
        class NoBody(Response):
            def read1(self, size):
                raise TimeoutError()
        record, raw = source.fetch(source.BASE, 10, Opener([NoBody(b'')]))
        self.assertIsNone(raw)
        self.assertEqual(record['status'], 200)
        self.assertEqual(record['bytes_read'], 0)
        self.assertNotIn('first_byte_ms', record)

    def test_content_length_mismatch_and_oversize_stop(self):
        for length, reason in (('9', 'content_length_mismatch'), ('11', 'response_too_large'), ('invalid', 'invalid_content_length')):
            response = Response(b'abc')
            response.headers['Content-Length'] = length
            record, raw = source.fetch(source.BASE, 10, Opener([response]))
            self.assertEqual(record['result'], reason)
            self.assertIsNone(raw)
            self.assertFalse(record['body_complete'])

    def test_total_budget_stops_even_when_chunks_keep_arriving(self):
        current = [0]
        class Slow(Response):
            def read1(self, size):
                current[0] += 16
                return super().read1(1)
        record, raw = source.fetch(source.BASE, 10, Opener([Slow(b'abcdef')]), clock=lambda: current[0])
        self.assertEqual(record['result'], 'request_budget_exceeded')
        self.assertEqual(record['bytes_read'], 2)
        self.assertIsNone(raw)

    def test_complete_body_with_declared_length(self):
        response = Response(b'abc')
        response.headers['Content-Length'] = '3'
        record, raw = source.fetch(source.BASE, 10, Opener([response]))
        self.assertEqual(raw, b'abc')
        self.assertTrue(record['body_complete'])
        self.assertEqual(record['phase'], 'complete')

    def test_optional_check_uses_saved_real_metadata_adapter(self):
        raw = (Path(__file__).absolute().parents[2]/'tests/fixtures/wenku8/details/4151.htm').read_bytes()
        result = source.probe('local', Opener([Response(b'User-agent: *'), Response(raw, 'text/html')]),
                              lambda _: None, source.check_metadata)
        self.assertEqual(result['metadata']['book_id'], 4151)
        self.assertTrue(result['metadata']['tags_complete'])
        self.assertEqual(result['outcome'], 'detail_identity_verified_optional_fields_unvalidated')
        self.assertNotIn('introduction', result['metadata'])

    def test_200_login_page_is_not_valid_identity(self):
        result = source.probe('local', Opener([Response(b'User-agent: *'), Response(b'<html>login</html>', 'text/html')]),
                              lambda _: None, source.check_metadata)
        self.assertEqual(result['stop_reason'], 'metadata_check_failed')

    def test_cli_json_is_portable_across_console_encodings(self):
        output = io.StringIO()
        result = {'outcome': 'detail_http_received_metadata_unverified', 'title': '标题与表情📚'}
        with patch('sys.argv', ['probe', '--location', 'local']), patch.object(source, 'probe', return_value=result), redirect_stdout(output):
            self.assertEqual(source.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        output.getvalue().encode('ascii')
