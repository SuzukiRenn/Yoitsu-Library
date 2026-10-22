from datetime import date, datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from lxml import html

from integrations.wenku8.session import SourceStop, TransientNetworkError
from integrations.wenku8.retry_policy import next_check, reviewed_initial_network_stop
from integrations.wenku8.updates import discover, listing_page
from booklist.source_updates import commit, prepare_snapshot, run
from booklist.tests.test_cover_cache import png, URL
from booklist import cover_cache
from booklist.models import Work, PublicRecommendation, PublicLike


class FrozenDateTime(datetime):
    current = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz is not None else cls.current.replace(tzinfo=None)


def page(rows, pages=(1,)):
    return html.fromstring('<html><body>'+''.join(
        '<div><div><a href="/book/%s.htm"><img></a></div><div><a href="/book/%s.htm"><b>%s</b></a>'
        '<p>更新：%s</p><a href="/book/%s.htm">详情</a></div></div>' % (pk, pk, title, day, pk)
        for pk, title, day in rows)+''.join(
            '<a href="/modules/article/toplist.php?sort=lastupdate&amp;page=%s">%s</a>' % (n, n)
            for n in pages)+'</body></html>')


class ListingTests(SimpleTestCase):
    def test_next_check_uses_next_calendar_day_in_china(self):
        self.assertEqual(next_check(datetime(2026, 10, 22, 2, tzinfo=timezone.utc)), '2026-10-23T04:00:00+08:00')
        self.assertEqual(next_check(datetime(2026, 10, 31, 18, tzinfo=timezone.utc)), '2026-11-02T04:00:00+08:00')

    def test_reviewed_recovery_accepts_only_the_diagnosed_initial_transient_stop(self):
        attempt = {'url': 'https://www.wenku8.net/modules/article/toplist.php?sort=lastupdate&page=2',
                   'status': 200, 'result': 'transport_failed', 'error_class': 'TimeoutError'}
        state = {'status': 'needs_attention', 'reason': 'source_transport_failed_after_bounded_retries',
                 'requests': [dict(attempt) for _ in range(3)]}
        self.assertTrue(reviewed_initial_network_stop(state))
        for changed in ({'items': [{'status': 'imported'}]}, {'reason': 'source_access_restricted'},
                        {'requests': [{**attempt, 'status': 403}]*3},
                        {'requests': [{**attempt, 'error_class': 'SSLCertVerificationError'}]*3}):
            self.assertFalse(reviewed_initial_network_stop({**state, **changed}))

    def test_transient_failure_waits_until_next_day_without_losing_discovery_boundary(self):
        with TemporaryDirectory() as directory, override_settings(DATA_DIR=Path(directory)), patch('booklist.source_updates.datetime', FrozenDateTime):
            FrozenDateTime.current = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)
            with patch('booklist.source_updates.Client') as client, patch('booklist.source_updates.discover', side_effect=TransientNetworkError('source_transport_failed_after_bounded_retries')):
                client.return_value.requests = []
                result = run(Path(directory)/'credentials')
                self.assertEqual(result['status'], 'network_wait')
                self.assertEqual(result['next_attempt_at'], '2026-10-23T04:00:00+08:00')
            checkpoint = Path(directory)/'source_updates/daily.json'
            state = json.loads(checkpoint.read_text())
            self.assertEqual(state['initial_cutoff'], '2026-10-20')
            with patch('booklist.source_updates.Client') as client:
                self.assertEqual(run(Path(directory)/'credentials')['status'], 'network_wait')
                client.assert_not_called()
            FrozenDateTime.current = datetime(2026, 10, 22, 20, 1, tzinfo=timezone.utc)
            with patch('booklist.source_updates.Client') as client, patch('booklist.source_updates.discover', return_value=[]) as scan:
                client.return_value.requests = []
                self.assertEqual(run(Path(directory)/'credentials')['status'], 'completed')
                self.assertEqual(scan.call_args.args[1], date(2026, 10, 20))
            self.assertEqual(len(list((Path(directory)/'source_updates').glob('network-wait-*.json'))), 1)

    def test_duplicate_image_title_detail_links_become_one_dated_item(self):
        rows, pages = listing_page(page([(1, '作品甲', '2026-10-22'), (2, '作品乙', '2026-10-21')], (1, 2)), date(2026, 10, 22))
        self.assertEqual([row['title'] for row in rows], ['作品甲', '作品乙'])
        self.assertEqual(set(pages), {1, 2})

    def test_observed_source_bold_parent_of_title_link(self):
        root = page([(1531, '真实标题嵌套', '2026-10-22')])
        anchor = root.xpath('//a[b]')[0]
        bold = anchor[0]
        text = bold.text
        anchor.remove(bold)
        anchor.text = text
        parent = anchor.getparent()
        position = parent.index(anchor)
        parent.remove(anchor)
        bold.text = None
        bold.append(anchor)
        parent.insert(position, bold)
        rows, _ = listing_page(root, date(2026, 10, 22))
        self.assertEqual(rows[0]['title'], '真实标题嵌套')

    def test_date_boundary_requires_observed_pagination_and_complete_scan(self):
        client = type('Client', (), {})()
        first = page([(1, '甲', '2026-10-22')], (1, 2))
        second = page([(2, '乙', '2026-10-19')], (1, 2, 3))
        with patch.object(client, 'listing', create=True, side_effect=[first, second]) as fetch:
            rows = discover(client, date(2026, 10, 20), date(2026, 10, 22))
            self.assertEqual([row['book_id'] for row in rows], [1])
            self.assertEqual(fetch.call_count, 2)
        with patch.object(client, 'listing', create=True, return_value=first):
            with self.assertRaisesRegex(SourceStop, 'page_budget'):
                discover(client, date(2026, 10, 20), date(2026, 10, 22), max_pages=1)
        with patch.object(client, 'listing', create=True, return_value=page([(1, '甲', '2026-10-22')], (1, 3))):
            with self.assertRaisesRegex(SourceStop, 'next_page_not_observed'):
                discover(client, date(2026, 10, 20), date(2026, 10, 22))

    def test_future_dates_and_order_changes_stop(self):
        for rows in [[(1, '甲', '2026-10-23')], [(1, '甲', '2026-10-20'), (2, '乙', '2026-10-21')]]:
            with self.assertRaises(SourceStop):
                listing_page(page(rows), date(2026, 10, 22))

    def test_dry_run_never_imports_and_safety_stop_is_persistent(self):
        with TemporaryDirectory() as directory, override_settings(DATA_DIR=Path(directory)):
            with patch('booklist.source_updates.Client') as client, patch('booklist.source_updates.discover', return_value=[]), patch('booklist.source_updates.commit') as write:
                client.return_value.requests = []
                self.assertEqual(run(Path(directory)/'credentials', dry_run=True)['status'], 'dry_run')
                write.assert_not_called()
                self.assertFalse((Path(directory)/'source_updates/daily.json').exists())
                client.return_value.robots.side_effect = SourceStop('source_access_restricted')
                self.assertEqual(run(Path(directory)/'credentials')['status'], 'needs_attention')
            with patch('booklist.source_updates.Client') as client:
                self.assertEqual(run(Path(directory)/'credentials')['reason'], 'source_access_restricted')
                client.assert_not_called()


class ImportTests(TestCase):
    def snapshot(self):
        names = ('title', 'author', 'introduction', 'cover_url', 'publisher_category', 'status', 'last_updated', 'word_count', 'tags')
        fields = {key: {'state': 'empty', 'value': None} for key in names}
        fields['title'] = {'state': 'value', 'value': '更新作品'}
        fields['author'] = {'state': 'value', 'value': '新作者'}
        fields['last_updated'] = {'state': 'value', 'value': '2026-10-22'}
        fields['tags']['complete'] = True
        return {'version': 1, 'book_id': 4151, 'source_url': 'https://www.wenku8.net/book/4151.htm',
                'fields': fields, 'document_diagnostics': {'complete': True}}

    def test_source_refresh_preserves_overrides_visibility_and_public_contributions(self):
        work = Work.objects.create(wenku8_book_id=4151, wenku8_title='旧作品',
            identity_status='matched', title_override='管理员标题', source_metadata={'last_updated': '2026-10-21', 'custom': 'preserved'})
        from booklist import services
        _, person = services.issue_anonymous()
        PublicRecommendation.objects.create(work=work, anonymous_client=person)
        PublicLike.objects.create(work=work, anonymous_client=person)
        counts = (PublicRecommendation.objects.count(), PublicLike.objects.count())
        receipt = commit(self.snapshot(), None)
        work.refresh_from_db()
        self.assertFalse(receipt['created'])
        self.assertEqual(work.title_override, '管理员标题')
        self.assertEqual(work.wenku8_author, '新作者')
        self.assertEqual(work.source_metadata['custom'], 'preserved')
        self.assertIsNone(work.public_added_at)
        self.assertEqual((PublicRecommendation.objects.count(), PublicLike.objects.count()), counts)

    def test_cover_pointer_and_source_import_commit_together(self):
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            cover = cover_cache.store_version(URL, png(), 'image/png')
            receipt = commit(self.snapshot(), cover)
            self.assertEqual(Work.objects.get(pk=receipt['work_id']).source_metadata['cover_cache_version'], cover)

    def test_incomplete_detail_never_reaches_automatic_import(self):
        snapshot = self.snapshot()
        row = {'book_id': 4151, 'source_url': snapshot['source_url'], 'date': '2026-10-22'}
        with patch('booklist.source_updates.extract', return_value=snapshot):
            snapshot['document_diagnostics']['complete'] = False
            with self.assertRaisesRegex(SourceStop, 'detail_incomplete'):
                prepare_snapshot(b'fixture', row)

    def test_network_interruption_keeps_committed_work_and_recheck_is_idempotent(self):
        rows = [{'book_id': n, 'source_url': 'https://www.wenku8.net/book/%s.htm' % n,
                 'date': '2026-10-22', 'title': '作品%s' % n} for n in (4151, 4152)]
        def prepared(raw, row):
            snapshot = self.snapshot()
            snapshot.update(book_id=row['book_id'], source_url=row['source_url'])
            snapshot['fields']['title']['value'] = row['title']
            return snapshot, snapshot['fields'], []
        with TemporaryDirectory() as directory, override_settings(DATA_DIR=Path(directory)), patch('booklist.source_updates.datetime', FrozenDateTime), patch('booklist.source_updates.prepare_snapshot', side_effect=prepared):
            FrozenDateTime.current = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)
            with patch('booklist.source_updates.Client') as client, patch('booklist.source_updates.discover', return_value=rows):
                client.return_value.requests = []
                client.return_value.request.side_effect = [({'media_type': 'text/html'}, b'fixture'), TransientNetworkError('source_transport_failed_after_bounded_retries')]
                self.assertEqual(run(Path(directory)/'credentials')['status'], 'network_wait')
            self.assertEqual(Work.objects.count(), 1)
            checkpoint = json.loads((Path(directory)/'source_updates/daily.json').read_text())
            self.assertEqual([item['status'] for item in checkpoint['items']], ['imported', 'pending'])
            FrozenDateTime.current = datetime(2026, 10, 22, 20, 1, tzinfo=timezone.utc)
            with patch('booklist.source_updates.Client') as client, patch('booklist.source_updates.discover', return_value=rows):
                client.return_value.requests = []
                client.return_value.request.return_value = ({'media_type': 'text/html'}, b'fixture')
                self.assertEqual(run(Path(directory)/'credentials')['status'], 'completed')
            self.assertEqual(Work.objects.count(), 2)
            self.assertEqual(PublicRecommendation.objects.count(), 0)
            self.assertEqual(PublicLike.objects.count(), 0)
