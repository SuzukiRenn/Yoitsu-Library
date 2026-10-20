"""Offline transport substitutes and real saved metadata; never live source calls."""
from email.message import Message
import io
from http.client import IncompleteRead
import json
import os
import ssl
from urllib.error import URLError
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.db import DatabaseError
from django.test import TestCase
from booklist import collecting
from booklist.models import Work, PublicRecommendation, PublicLike
from integrations.wenku8.offline import SnapshotError

FIXTURES = Path(__file__).absolute().parents[2]/'tests/fixtures/wenku8/details'
ROBOTS = b'User-agent: *\nDisallow:\n'


class Response(io.BytesIO):
    status = 200
    def __init__(self, raw, media='text/html'):
        super().__init__(raw)
        self.headers = Message(); self.headers['Content-Type'] = media


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses); self.urls = []
    def open(self, request, timeout):
        self.urls.append(request.full_url)
        if not self.responses:
            raise AssertionError('Unexpected network attempt')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def robots(raw=ROBOTS):
    return Response(raw, 'text/plain')


def detail(book_id=4151):
    return Response((FIXTURES/f'{book_id}.htm').read_bytes())


class CollectionTests(TestCase):
    def test_incomplete_response_is_discarded_and_retried_from_start(self):
        class InterruptedResponse(Response):
            def read1(self, size=-1):
                raise IncompleteRead(b'partial untrusted content', 200)
        state = collecting.new_state([4151]); sleeps = []
        opener = Opener(robots(), InterruptedResponse(b''), detail())
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append, network_retries=2)
            self.assertTrue(result['success'])
            self.assertEqual(sleeps, [10, 10, 30])
            self.assertEqual(state['requests'][1]['error_class'], 'IncompleteRead')
            self.assertEqual(state['requests'][1]['status'], 200)
            self.assertEqual(Work.objects.count(), 1)
            self.assertNotIn('untrusted', json.dumps(state))

    def test_opt_in_retry_recovers_tls_and_robot_timeouts_with_bounded_backoff(self):
        state = collecting.new_state([4151]); sleeps = []
        opener = Opener(TimeoutError('private address'), robots(),
                        URLError(ssl.SSLEOFError(8, 'private credentials')), detail())
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append, network_retries=2)
            self.assertTrue(result['success'])
            self.assertEqual(sleeps, [10, 30, 10, 30])
            self.assertEqual([r['attempt_number'] for r in state['requests']], [1, 2, 1, 2])
            self.assertNotIn('network_wait', state)
            self.assertNotIn('private', json.dumps(state))
            self.assertEqual(Work.objects.count(), 1)

    def test_retry_exhaustion_stops_robots_before_detail_and_keeps_checkpoint(self):
        state = collecting.new_state([4151]); sleeps = []
        opener = Opener(*[TimeoutError('sensitive error') for _ in range(3)])
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append, network_retries=2)
            self.assertEqual(result['stop_reason'], 'robots_fetch_failed')
            self.assertEqual(sleeps, [10, 30, 60])
            self.assertEqual(len(opener.urls), 3)
            self.assertTrue(all(url.endswith('/robots.txt') for url in opener.urls))
            self.assertEqual(state['items'][0]['status'], 'pending')
            self.assertEqual(Work.objects.count(), 0)

    def test_access_restrictions_certificate_and_parser_failures_are_not_retried(self):
        for code in (301, 401, 403, 429, 503):
            self.assertFalse(collecting.retryable_transport({'status': code, 'result': 'http_status_stop'}))
        self.assertFalse(collecting.retryable_transport({'status': None, 'result': 'transport_failed',
            'error_class': 'URLError', 'cause_class': 'SSLCertVerificationError'}))
        state = collecting.new_state([4151]); opener = Opener(robots(), Response(b'<html>login</html>'))
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=lambda _: None, network_retries=2)
            self.assertEqual(result['counts']['failed'], 1)
            self.assertEqual(len(opener.urls), 2)

    def test_transport_retry_honors_robots_delay_and_does_not_fetch_success_twice(self):
        state = collecting.new_state([4151]); sleeps = []
        opener = Opener(robots(b'User-agent: *\nCrawl-delay: 40\nDisallow:\n'), TimeoutError(), detail())
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append, network_retries=2)
            self.assertTrue(result['success'])
            self.assertEqual(sleeps, [10, 40, 40])
            self.assertEqual(Work.objects.count(), 1)
            self.assertEqual(len(opener.urls), 3)

    def test_windows_checkpoint_replace_recovers_from_short_file_lock(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'state.json'
            original = os.replace
            calls = []
            def briefly_locked(source, target):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError('temporary local file lock')
                return original(source, target)
            with patch('booklist.collecting.os.name', 'nt'), patch('booklist.collecting.os.replace', side_effect=briefly_locked), patch('booklist.collecting.time.sleep') as wait:
                collecting.atomic_json(path, {'saved': True})
            self.assertEqual(json.loads(path.read_text()), {'saved': True})
            self.assertEqual(wait.call_count, 2)

    def test_collection_lock_is_exclusive_and_released(self):
        with TemporaryDirectory() as folder:
            with collecting.collection_lock(Path(folder)):
                with self.assertRaises(SnapshotError):
                    with collecting.collection_lock(Path(folder)):
                        self.fail('Concurrent collector acquired the lock')
            with collecting.collection_lock(Path(folder)):
                pass

    def test_cli_initializes_isolated_directory_before_validating_ids(self):
        root = Path(__file__).absolute().parents[2]
        with TemporaryDirectory() as folder:
            untouched = Path(folder)/'do-not-use'
            result = subprocess.run([sys.executable, '-B', str(root/'scripts/collect_wenku8.py'), '--ids', '0'],
                cwd=root, env={**os.environ, 'BOOKLIST_DATA_DIR': str(untouched)},
                capture_output=True, encoding='utf-8', errors='replace', timeout=30)
            self.assertEqual(result.returncode, 1)
            report, _ = json.JSONDecoder().raw_decode(result.stdout)
            self.assertEqual(report.get('error_class'), 'SnapshotError')
            self.assertFalse(untouched.exists())

    def test_discovery_cli_rejects_more_than_three_seeds_before_network(self):
        root = Path(__file__).absolute().parents[2]
        result = subprocess.run([sys.executable, '-B', str(root/'scripts/discover_wenku8.py'),
            '--seed-ids', '1', '2', '3', '4'], cwd=root,
            capture_output=True, encoding='utf-8', errors='replace', timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('1..3 explicit seed IDs', result.stderr)

    def test_two_items_import_resume_idempotency_and_no_public_contributions(self):
        state = collecting.new_state([4151, 2951])
        sleeps = []; opener = Opener(robots(), detail(), detail(2951))
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append)
            self.assertTrue(result['success'], result)
            self.assertEqual(result['counts']['imported'], 2)
            self.assertEqual(sleeps, [10, 10, 10])
            self.assertEqual(Work.objects.count(), 2)
            self.assertEqual(result['public_contributions'], 0)
            self.assertFalse(Work.objects.filter(public_added_at__isnull=False).exists())
            self.assertFalse(PublicRecommendation.objects.exists()); self.assertFalse(PublicLike.objects.exists())
            empty = Opener()
            self.assertTrue(collecting.run(folder, state, opener=empty, sleeper=sleeps.append)['success'])
            self.assertEqual(empty.urls, [])
            self.assertFalse(list(Path(folder).glob('*.htm*')))
            self.assertNotIn('<html', (Path(folder)/'state.json').read_text())
            again = collecting.new_state([4151])
            collecting.run(folder, again, opener=Opener(robots(), detail()), sleeper=sleeps.append)
            self.assertEqual(Work.objects.count(), 2)

    def test_bounded_run_resumes_only_pending_and_respects_longer_policy(self):
        state = collecting.new_state([4151, 2951]); sleeps = []
        policy = b'User-agent: *\nCrawl-delay: 20\nDisallow:\n'
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, max_items=1, opener=Opener(robots(policy), detail()), sleeper=sleeps.append)
            self.assertFalse(result['success']); self.assertEqual(result['counts']['pending'], 1)
            opener = Opener(robots(policy), detail(2951))
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append)
            self.assertTrue(result['success'])
            self.assertEqual(opener.urls[-1], 'https://www.wenku8.net/book/2951.htm')
            self.assertEqual(sleeps, [10,20,10,20])

    def test_denied_policy_and_failed_transport_stop_without_retry_or_html(self):
        with TemporaryDirectory() as folder:
            state = collecting.new_state([4151])
            opener = Opener(robots(b'User-agent: *\nDisallow: /book/\n'))
            result = collecting.run(folder, state, opener=opener, sleeper=lambda _: None)
            self.assertEqual(result['stop_reason'], 'robots_disallows_detail')
            self.assertEqual(len(opener.urls), 1)
            state = collecting.new_state([4151,2951])
            opener = Opener(robots(), TimeoutError('private proxy credential'))
            result = collecting.run(folder, state, opener=opener, sleeper=lambda _: None)
            self.assertEqual(result['counts']['failed'], 1)
            self.assertEqual(result['counts']['pending'], 1)
            self.assertEqual(len(opener.urls), 2)
            self.assertNotIn('credential', json.dumps(state))
            self.assertEqual(Work.objects.count(), 0)

    def test_login_or_truncated_detail_never_imports_and_retry_is_explicit(self):
        with TemporaryDirectory() as folder:
            state = collecting.new_state([4151])
            result = collecting.run(folder, state, opener=Opener(robots(), Response(b'<html>login</html>')), sleeper=lambda _: None)
            self.assertEqual(result['counts']['failed'], 1)
            self.assertFalse(Work.objects.exists())
            empty = Opener()
            collecting.run(folder, state, opener=empty, sleeper=lambda _: None)
            self.assertEqual(empty.urls, [])
            result = collecting.run(folder, state, retry_failed=True, opener=Opener(robots(), detail()), sleeper=lambda _: None)
            self.assertTrue(result['success'])
            state = collecting.new_state([2951])
            raw = (FIXTURES/'2951.htm').read_bytes().rsplit(b'</body>',1)[0]
            result = collecting.run(folder, state, opener=Opener(robots(), Response(raw)), sleeper=lambda _: None)
            self.assertEqual(result['stop_reason'], 'detail_document_incomplete')
            self.assertEqual(Work.objects.count(), 1)

    def test_database_interruption_resumes_fetched_metadata_without_network(self):
        with TemporaryDirectory() as folder:
            state = collecting.new_state([4151])
            with patch('booklist.collecting.apply_snapshot', side_effect=DatabaseError):
                result = collecting.run(folder, state, opener=Opener(robots(), detail()), sleeper=lambda _: None)
            self.assertEqual(result['counts']['fetched'], 1)
            self.assertEqual(result['stop_reason'], 'database_write_failed')
            opener = Opener()
            result = collecting.run(folder, state, opener=opener, sleeper=lambda _: None)
            self.assertTrue(result['success']); self.assertEqual(opener.urls, [])

    def test_fingerprint_and_snapshot_tampering_are_rejected(self):
        state = collecting.new_state([4151])
        with TemporaryDirectory() as folder:
            with patch('booklist.collecting.apply_snapshot', side_effect=DatabaseError):
                collecting.run(folder, state, opener=Opener(robots(), detail()), sleeper=lambda _: None)
            (Path(folder)/'4151.metadata.json').write_text('{}')
            result = collecting.run(folder, state, opener=Opener(), sleeper=lambda _: None)
            self.assertEqual(result['stop_reason'], 'cached_metadata_changed')
            self.assertFalse(Work.objects.exists())
            with patch('booklist.collecting.code_fingerprint', return_value='changed'), self.assertRaises(SnapshotError):
                collecting.run(folder, state, opener=Opener(), sleeper=lambda _: None)

    def test_listing_discovery_whitelists_origin_and_caps_twenty_real_links(self):
        url = 'https://www.wenku8.net/modules/article/toplist.php?sort=lastupdate'
        raw = ('<html><head><meta charset="utf-8"></head><body><a href="https://evil.invalid/book/999.htm">bad</a>'+
               ''.join(f'<a href="/book/{index}.htm">title</a>' for index in range(1,30))+'</body></html>').encode()
        self.assertEqual(collecting.discover_ids(raw,url), list(range(1,21)))
        self.assertEqual(collecting.discover_ids(raw,url,[1,2,3]), list(range(4,24)))
        self.assertEqual(collecting.listing_url('https://www.wenku8.net/'), 'https://www.wenku8.net/')
        state = collecting.new_state(discover='https://www.wenku8.net/', exclude_ids=[1,2,3])
        self.assertEqual(state['exclude_ids'], [1,2,3])
        with self.assertRaises(SnapshotError):
            collecting.new_state([1], exclude_ids=[2])
        for invalid in ('https://evil.invalid/modules/article/articlelist.php', 'http://www.wenku8.net/modules/article/articlelist.php',
                        url+'&page=2', 'https://www.wenku8.net/login.php'):
            with self.assertRaises(SnapshotError):
                collecting.listing_url(invalid)
        for ids in ([True], [1,1], list(range(1,22)), [0]):
            with self.assertRaises(SnapshotError):
                collecting.new_state(ids)

    def test_new_source_update_preserves_manual_override(self):
        with TemporaryDirectory() as folder:
            state = collecting.new_state([4151])
            collecting.run(folder, state, opener=Opener(robots(),detail()), sleeper=lambda _: None)
            work = Work.objects.get(); work.title_overridden=True; work.title_override='人工书名'; work.save()
            collecting.run(folder, collecting.new_state([4151]), opener=Opener(robots(),detail()), sleeper=lambda _: None)
            work.refresh_from_db(); self.assertEqual(work.display_title,'人工书名')
