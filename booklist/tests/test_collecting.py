"""Offline transport substitutes and real saved metadata; never live source calls."""
from email.message import Message
import io
import json
import os
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

    def test_two_items_import_resume_idempotency_and_no_public_contributions(self):
        state = collecting.new_state([4151, 2951])
        sleeps = []; opener = Opener(robots(), detail(), detail(2951))
        with TemporaryDirectory() as folder:
            result = collecting.run(folder, state, opener=opener, sleeper=sleeps.append)
            self.assertTrue(result['success'])
            self.assertEqual(result['counts']['imported'], 2)
            self.assertEqual(sleeps, [15, 15, 15])
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
            self.assertEqual(sleeps, [15,20,15,20])

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
