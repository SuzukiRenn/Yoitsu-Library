import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zlib
from django.test import SimpleTestCase, TestCase, override_settings
from booklist import catalog, cover_cache
from scripts import cache_wenku8_covers as worker

URL = 'http://img.wenku8.com/image/4/4151/4151s.png'


def png(width=1, height=1, rgba=b'\xff\x00\x00\xff'):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))+chunk(
        b'IDAT', zlib.compress((b'\x00'+rgba*width)*height))+chunk(b'IEND', b'')


class CoverCacheTests(SimpleTestCase):
    def test_same_url_refresh_keeps_old_cover_and_exposes_immutable_new_revision(self):
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            old = png(); new = png(2, 1)
            cover_cache.store(URL, old, 'image/png')
            revision = cover_cache.store_version(URL, new, 'image/png')
            pinned = '/media/covers/'+revision['name']
            self.assertEqual((Path(directory)/cover_cache.filename(URL)).read_bytes(), old)
            self.assertEqual((Path(directory)/revision['name']).read_bytes(), new)
            self.assertEqual(cover_cache.local_url(pinned), pinned)
            self.assertNotEqual(pinned, cover_cache.local_url(URL))
            with self.assertRaises(ValueError):
                cover_cache.store_version(URL, b'broken', 'image/png')
            self.assertEqual((Path(directory)/revision['name']).read_bytes(), new)

    def test_allowlist_prevents_arbitrary_hosts_redirect_targets_and_path_guesses(self):
        self.assertEqual(cover_cache.source_url(URL), URL.replace('http:', 'https:'))
        self.assertEqual(cover_cache.filename(URL), cover_cache.filename(URL.replace('http:', 'https:')))
        for value in ('https://127.0.0.1/cover.jpg', URL.replace('img.wenku8.com', 'img.wenku8.com.evil.test'),
                      URL+'?redirect=evil', URL+'#fragment', URL.replace('/4/', '/3/'),
                      URL.replace('img.wenku8.com', 'user@img.wenku8.com'), URL.replace('img.wenku8.com', 'img.wenku8.com:443')):
            self.assertIsNone(cover_cache.source_url(value))
        self.assertIsNone(cover_cache.cache_info('../settings.py'))

    def test_validated_atomic_cache_serves_local_image_and_browser_cache_headers(self):
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            self.assertEqual(catalog.cover_url(URL), URL.replace('http:', 'https:'))
            raw = png(); info = cover_cache.store(URL, raw, 'image/png')
            self.assertEqual((info['width'], info['height'], info['bytes']), (1, 1, len(raw)))
            local = cover_cache.local_url(URL)
            self.assertEqual(catalog.cover_url(URL), local)
            from django.conf import settings
            from secrets import token_urlsafe
            self.client.cookies[settings.ADMIN_COOKIE] = token_urlsafe(32)
            self.client.cookies[settings.ANONYMOUS_COOKIE] = token_urlsafe(32)
            with patch('scripts.probe_wenku8.fetch', side_effect=AssertionError('Web views must not access source')):
                response = self.client.get(local)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b''.join(response.streaming_content), raw)
                self.assertEqual(response['Content-Type'], 'image/png')
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(response['Cache-Control'], 'public, max-age=86400')
                self.assertEqual(self.client.get(local, HTTP_IF_NONE_MATCH=response['ETag']).status_code, 304)
                self.client.cookies.clear()
                self.assertEqual(self.client.post(local).status_code, 405)
            self.assertFalse(list(Path(directory).glob('.cover-*')))
            (Path(directory)/cover_cache.filename(URL)).unlink()
            self.assertIsNone(cover_cache.local_url(URL))
            self.assertEqual(self.client.get(local).status_code, 404)
            self.assertEqual(catalog.cover_url(URL), URL.replace('http:', 'https:'))

    def test_html_svg_truncation_corrupt_png_and_large_images_never_become_cached_files(self):
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            for raw, media in ((b'<html>login</html>', 'image/png'), (b'<svg/>', 'image/svg+xml'),
                               (png()[:-4], 'image/png'), (png(4097), 'image/png'),
                               (png().replace(b'IDAT', b'IDAX'), 'image/png'), (b'x'*(cover_cache.MAX_BYTES+1), 'image/png')):
                with self.assertRaises(ValueError): cover_cache.store(URL, raw, media)
                self.assertFalse(list(Path(directory).iterdir()))

    def test_jpeg_dimensions_and_end_marker_are_required(self):
        frame = b'\xff\xc0'+struct.pack('>H', 11)+b'\x08'+struct.pack('>HH', 3, 2)+b'\x01\x01\x11\x00'
        scan = b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00'
        raw = b'\xff\xd8'+frame+scan+b'\x00\xff\xd9'
        self.assertEqual(cover_cache.validate_image(raw, 'jpg', 'image/jpeg'), {'width': 2, 'height': 3})
        for invalid in (raw[:-2], b'\xff\xd8\xff\xd9'):
            with self.assertRaises(ValueError): cover_cache.validate_image(invalid, 'jpg', 'image/jpeg')

    def test_transient_retry_is_bounded_and_restrictions_are_not_retried(self):
        failed = {'result': 'transport_failed', 'status': None, 'cause_class': 'SSLEOFError'}
        success = {'result': 'received', 'status': 200, 'media_type': 'image/png'}
        state = {}; delays = []
        with patch.object(worker.transport, 'fetch', side_effect=[(failed, None), (success, png())]) as fetch:
            record, raw, attempts = worker.fetch(URL, 1024, object(), state, lambda: None, 2, delays.append)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(delays, [2, 30]); self.assertEqual(raw, png())
            self.assertNotIn('network_wait', state)
        with patch.object(worker.transport, 'fetch', return_value=(failed, None)) as fetch:
            worker.fetch(URL, 1024, object(), state, lambda: None, 2, delays.append)
            self.assertEqual(fetch.call_count, 3)
        for code in (301, 401, 403, 429, 503):
            self.assertFalse(worker.retryable({'status': code, 'result': 'transport_failed', 'cause_class': 'SSLEOFError'}))

    def test_image_robots_policy_and_stricter_delay(self):
        rules, interval = worker.robots_policy({'status': 200, 'media_type': 'text/plain'}, b'# comments only\n')
        self.assertTrue(rules.can_fetch(worker.transport.AGENT, URL)); self.assertEqual(interval, 2)
        rules, interval = worker.robots_policy({'status': 200, 'media_type': 'text/plain'}, b'User-agent: *\nCrawl-delay: 12\nDisallow: /private/\n')
        self.assertEqual(interval, 12)
        self.assertFalse(rules.can_fetch(worker.transport.AGENT, 'https://img.wenku8.com/private/x'))
        with self.assertRaises(RuntimeError): worker.robots_policy({'status': 403}, None)


class CachedActivityCoverTests(TestCase):
    def test_current_cover_updates_without_changing_a_pinned_activity(self):
        from datetime import timedelta
        from django.utils import timezone
        from booklist import activities, services
        from booklist.models import Activity, ActivityWork, Work
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            first = cover_cache.store_version(URL, png(), 'image/png')
            work = Work.objects.create(original_submitted_title='版本封面作品', wenku8_cover_url=URL,
                source_metadata={'cover_cache_version': first})
            actor = services.initialize_admin('owner', 'Cover-Revision!Testing-Password-49')
            _, person = services.issue_anonymous()
            now = timezone.now()
            period = Activity.objects.create(title='固定封面活动', start_at=now-timedelta(hours=1),
                started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(hours=1),
                status='active', created_by=actor)
            activities.submit(period.pk, person, {'work_id':work.pk,'title':work.display_title,
                'reason':'','copy_public_reason':False})
            pinned = ActivityWork.objects.get().snapshot_cover_url
            self.assertEqual(pinned, '/media/covers/'+first['name'])
            second = cover_cache.store_version(URL, png(rgba=b'\x00\xff\x00\xff'), 'image/png')
            work.source_metadata['cover_cache_version'] = second
            work.save(update_fields=['source_metadata'])
            self.assertEqual(catalog.work_detail(work.pk)['cover_url'], '/media/covers/'+second['name'])
            self.assertEqual(activities.listing(period.pk)['results'][0]['cover_url'], pinned)
            self.assertNotEqual(first['name'], second['name'])

    def test_new_activity_snapshot_keeps_origin_url_when_cover_is_cached(self):
        from datetime import timedelta
        from django.utils import timezone
        from booklist import activities, services
        from booklist.models import Activity, ActivityWork, Work
        with TemporaryDirectory() as directory, override_settings(COVER_CACHE_DIR=Path(directory)):
            cover_cache.store(URL, png(), 'image/png')
            actor = services.initialize_admin('owner', 'Cached-Activity!Testing-Password-49')
            _, person = services.issue_anonymous()
            now = timezone.now()
            period = Activity.objects.create(title='封面缓存活动', start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
                scheduled_end_at=now+timedelta(hours=1), status='active', created_by=actor)
            work = Work.objects.create(original_submitted_title='封面作品', wenku8_cover_url=URL)
            activities.submit(period.pk, person, {'work_id': work.pk, 'title': work.display_title, 'reason': '', 'copy_public_reason': False})
            self.assertEqual(ActivityWork.objects.get().snapshot_cover_url, URL.replace('http:', 'https:'))
            self.assertEqual(activities.listing(period.pk)['results'][0]['cover_url'], cover_cache.local_url(URL))
