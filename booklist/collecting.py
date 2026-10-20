"""Bounded, resumable metadata collection into an isolated catalog.

Checkpoint only parsed metadata and transport diagnostics, never source HTML.
No login, redirects, cover downloads or novel text. Transport retries are bounded and opt-in.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit, parse_qsl
from urllib.request import build_opener

from django.db import connection, transaction
from django.db import DatabaseError
from .importing import apply_snapshot, validate_snapshot
from .models import Work, WorkTag, PublicRecommendation, PublicLike, Recommendation, Vote
from integrations.wenku8.offline import extract, fingerprint, parser, SnapshotError
from scripts import probe_wenku8 as source

MAX_ITEMS = 20
MIN_INTERVAL = 10


def code_fingerprint():
    digest = hashlib.sha256(fingerprint().encode())
    for path in (Path(__file__), Path(source.__file__), Path(__file__).with_name('importing.py')):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            # Windows readers/indexers can briefly hold the destination open.
            # Retry only the local atomic rename, never a source request.
            if os.name != 'nt' or attempt == 4:
                raise
            time.sleep(.05 * 2**attempt)


@contextmanager
def collection_lock(root):
    """OS-owned lock releases on interruption; retain the inode/file for safe reuse."""
    root.mkdir(parents=True, exist_ok=True)
    with (root/'.collector.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SnapshotError('another_collection_is_running') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def validate_ids(ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ITEMS:
        raise SnapshotError('one_to_twenty_book_ids_required')
    if any(type(value) is not int or not 1 <= value <= 99999999 for value in ids) or len(set(ids)) != len(ids):
        raise SnapshotError('invalid_or_duplicate_book_ids')
    return ids


def listing_url(url):
    value = urlsplit(url)
    if value.scheme != 'https' or value.netloc != 'www.wenku8.net' or value.fragment:
        raise SnapshotError('invalid_listing_url')
    allowed = {'/': set(), '/modules/article/articlelist.php': {'fullflag'}, '/modules/article/toplist.php': {'sort'}}
    query = parse_qsl(value.query, keep_blank_values=True)
    if value.path not in allowed or len(query) > 1 or any(key not in allowed[value.path] for key, _ in query):
        raise SnapshotError('invalid_listing_url')
    if query and query[0][1] not in ({'0', '1'} if query[0][0] == 'fullflag' else {'allvisit','anime','lastupdate','postdate'}):
        raise SnapshotError('invalid_listing_url')
    return url


def discover_ids(raw, url, exclude_ids=()):
    """Only discover IDs; all identity/title/fields must come from validated detail pages."""
    p = parser()
    text, _ = p.LEGACY.decode_html(raw)
    root = p.html.fromstring(text, parser=p.html.HTMLParser(no_network=True, remove_comments=True))
    ids = []
    for href in root.xpath('//a/@href'):
        value = urlsplit(urljoin(url, href))
        match = re.fullmatch(r'/book/([1-9][0-9]{0,7})\.htm', value.path)
        if value.scheme == 'https' and value.netloc == 'www.wenku8.net' and not value.query and not value.fragment and match:
            book_id = int(match[1])
            if book_id not in ids and book_id not in exclude_ids:
                ids.append(book_id)
            if len(ids) == MAX_ITEMS:
                break
    if not ids:
        raise SnapshotError('listing_has_no_detail_links')
    return ids


def new_state(ids=None, discover=None, exclude_ids=None):
    if (ids is None) == (discover is None):
        raise SnapshotError('choose_ids_or_listing')
    if exclude_ids and discover is None:
        raise SnapshotError('exclusions_require_discovery')
    excluded = validate_ids(exclude_ids) if exclude_ids else []
    return {'version': 1, 'fingerprint': code_fingerprint(), 'created_at': datetime.now(timezone.utc).isoformat(),
            'listing_url': listing_url(discover) if discover else None,
            'exclude_ids': excluded,
            'items': [{'book_id': value, 'status': 'pending'} for value in validate_ids(ids)] if ids is not None else [],
            'requests': [], 'stop_reason': '', 'scope': 'isolated local catalog; metadata only; no public contributions'}


def validate_state(state):
    if state.get('version') != 1 or state.get('fingerprint') != code_fingerprint():
        raise SnapshotError('collector_or_parser_changed_start_new_batch')
    if state.get('listing_url'):
        listing_url(state['listing_url'])
    if state.get('exclude_ids'):
        validate_ids(state['exclude_ids'])
    if state.get('items'):
        validate_ids([item['book_id'] for item in state['items']])
    elif not state.get('listing_url'):
        raise SnapshotError('invalid_collection_state')
    if any(item.get('status') not in ('pending','fetched','imported','partial','failed') for item in state['items']):
        raise SnapshotError('invalid_collection_state')


def retryable_transport(record):
    """Never retry an HTTP restriction, certificate rejection, or parser failure."""
    if record.get('status') not in (None, 200) or record.get('result') != 'transport_failed':
        return False
    kinds = {'TimeoutError', 'ConnectionResetError', 'ConnectionAbortedError',
             'ConnectionRefusedError', 'SSLEOFError', 'RemoteDisconnected', 'gaierror', 'IncompleteRead'}
    return record.get('cause_class', record.get('error_class')) in kinds


def run(directory, state, *, max_items=MAX_ITEMS, retry_failed=False, network_retries=0, opener=None, sleeper=time.sleep, progress=lambda value: None):
    validate_state(state)
    if type(max_items) is not int or not 1 <= max_items <= MAX_ITEMS:
        raise SnapshotError('max_items_must_be_1_to_20')
    if type(network_retries) is not int or not 0 <= network_retries <= 3:
        raise SnapshotError('network_retries_must_be_0_to_3')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory/'state.json'
    save = lambda: atomic_json(checkpoint, state)
    save()
    pending = [item for item in state['items'] if item['status'] in ('pending','fetched') or (retry_failed and item['status'] == 'failed')]
    if state['items'] and not pending:
        return report(directory, state)
    state['stop_reason'] = ''
    state['retry_policy'] = {'network_retries': network_retries,
        'backoff_seconds': [30 * 2**n for n in range(network_retries)]}
    opener = opener or build_opener(source.NoRedirect())
    interval = MIN_INTERVAL
    rules = None
    parser()  # Check local dependencies before requesting anything.

    def request(url, limit):
        for number in range(network_retries + 1):
            delay = max(interval, 30 * 2**(number - 1)) if number else interval
            if number:
                state['network_wait'] = {'url': url, 'attempt': number + 1, 'delay_seconds': delay,
                    'due_at': (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()}
                save()
                progress({'book_id': None, 'status': 'network_backoff', 'attempt': number + 1,
                          'delay_seconds': delay})
            # Preserve ordinary request spacing and any stricter robots interval.
            sleeper(delay)
            state.pop('network_wait', None)
            attempt = {'url': url, 'result': 'started', 'attempt_number': number + 1,
                       'observed_at_utc': datetime.now(timezone.utc).isoformat()}
            state['requests'].append(attempt); save()
            record, raw = source.fetch(url, limit, opener)
            attempt.update(record); save()
            if raw is not None or not retryable_transport(record):
                return record, raw
        return record, raw

    # Resume cached metadata without any additional network request.
    needs_network = not state['items'] or any(item['status'] != 'fetched' for item in pending[:max_items])
    if needs_network:
        record, raw = request(source.BASE+'/robots.txt', source.ROBOTS_LIMIT)
        if raw is None:
            state['stop_reason'] = 'robots_fetch_failed'; save(); return report(directory, state)
        policy, rules = source.policy(raw, record['media_type'])
        state['robots'] = policy
        if rules is None or policy['minimum_interval_seconds'] > 60:
            state['stop_reason'] = policy['result'] if rules is None else 'robots_timing_requires_manual_schedule'
            save(); return report(directory, state)
        interval = max(MIN_INTERVAL, policy['minimum_interval_seconds'])

    if not state['items']:
        url = state['listing_url']
        if not rules.can_fetch(source.AGENT, url):
            state['stop_reason'] = 'robots_disallows_listing'; save(); return report(directory, state)
        record, raw = request(url, source.DETAIL_LIMIT)
        if raw is None or record['media_type'] != 'text/html':
            state['stop_reason'] = 'listing_fetch_failed'; save(); return report(directory, state)
        try:
            state['items'] = [{'book_id': value, 'status': 'pending'} for value in discover_ids(raw, url, state.get('exclude_ids', []))]
        except (SnapshotError, ValueError):
            state['stop_reason'] = 'listing_unrecognized'; save(); return report(directory, state)
        pending = state['items']
        save()

    for item in pending[:max_items]:
        book_id = item['book_id']; url = source.BASE+f'/book/{book_id}.htm'
        snapshot_path = directory/f'{book_id}.metadata.json'
        try:
            if item['status'] != 'fetched':
                if not rules.can_fetch(source.AGENT, url):
                    raise SnapshotError('robots_disallows_detail')
                record, raw = request(url, source.DETAIL_LIMIT)
                if raw is None or record['media_type'] != 'text/html':
                    raise SnapshotError('detail_fetch_failed')
                snapshot = extract(raw, book_id, url)
                if not snapshot['document_diagnostics']['complete']:
                    raise SnapshotError('detail_document_incomplete')
                validate_snapshot(snapshot)
                atomic_json(snapshot_path, snapshot)
                item.update(status='fetched', snapshot_hash=hashlib.sha256(snapshot_path.read_bytes()).hexdigest())
                save()
            else:
                raw_snapshot = snapshot_path.read_bytes()
                if hashlib.sha256(raw_snapshot).hexdigest() != item.get('snapshot_hash'):
                    raise SnapshotError('cached_metadata_changed')
                snapshot = json.loads(raw_snapshot)
            if snapshot.get('book_id') != book_id or snapshot.get('source_url') != url:
                raise SnapshotError('cached_identity_mismatch')
            # Checkpoint and DB cannot share a transaction. If interrupted after commit,
            # reapplying this saved source identity is idempotent and never creates contributions.
            with transaction.atomic():
                work, warnings, created = apply_snapshot(snapshot)
                if work.wenku8_book_id != book_id:
                    raise SnapshotError('import_identity_readback_failed')
            item.update(status='partial' if warnings else 'imported', work_id=work.pk,
                        title=work.display_title, warnings=warnings, created=created, error_code='')
            save(); progress({'book_id': book_id, 'status': item['status'], 'title': item['title']})
        except DatabaseError:
            state['stop_reason'] = 'database_write_failed'; save()
            break  # Preserve fetched metadata to retry locally, without another HTTP request.
        except (SnapshotError, OSError, ValueError) as exc:
            item.update(status='failed', error_code=str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__)
            state['stop_reason'] = item['error_code']; save()
            progress({'book_id': book_id, 'status': 'failed', 'error_code': item['error_code']})
            break  # No retry or identity/session changes; operator can explicitly resume later.
    return report(directory, state)


def report(directory, state):
    counts = {key: sum(item['status'] == key for item in state['items']) for key in ('pending','fetched','imported','partial','failed')}
    with connection.cursor() as cursor:
        integrity = cursor.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        foreign_keys = cursor.execute('PRAGMA foreign_key_check').fetchall() == []
    result = {'success': bool(state['items']) and not state['stop_reason'] and not any(counts[k] for k in ('pending','fetched','failed')) and integrity and foreign_keys,
              'counts': counts, 'stop_reason': state['stop_reason'], 'request_attempts': len(state['requests']),
              'integrity': integrity, 'foreign_keys_valid': foreign_keys,
              'catalog_count': Work.objects.count(), 'source_tag_links': WorkTag.objects.filter(source_type='source',state='active').count(),
              'public_contributions': PublicRecommendation.objects.count()+PublicLike.objects.count()+Recommendation.objects.count()+Vote.objects.count(),
              'items': state['items'], 'scope': state['scope']}
    atomic_json(Path(directory)/'summary.json', result)
    return result
