"""Incrementally cache observed Wenku8 cover URLs from the local acceptance catalog."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import build_opener
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_wenku8 as transport


def stamp(): return datetime.now(timezone.utc).isoformat()


def retryable(record):
    return record.get('result') == 'transport_failed' and record.get('status') in (None, 200) and record.get(
        'cause_class', record.get('error_class')) in {'TimeoutError', 'ConnectionResetError', 'ConnectionAbortedError',
            'ConnectionRefusedError', 'SSLEOFError', 'RemoteDisconnected', 'gaierror', 'IncompleteRead'}


def fetch(url, limit, opener, state, save, interval, sleeper=time.sleep):
    attempts = []
    for number, delay in enumerate((interval, max(interval, 30), max(interval, 60)), 1):
        state['network_wait'] = {'url': url, 'attempt': number, 'delay_seconds': delay}
        save(); sleeper(delay); state.pop('network_wait', None)
        record, raw = transport.fetch(url, limit, opener)
        attempts.append({**record, 'attempt_number': number})
        state['last_request'] = attempts[-1]; save()
        if raw is not None or not retryable(record): break
    return record, raw, attempts


def robots_policy(record, raw):
    rules = RobotFileParser('https://img.wenku8.com/robots.txt')
    if record.get('status') == 404:
        rules.parse([]); return rules, 2
    if raw is None or record.get('media_type') != 'text/plain':
        raise RuntimeError('image_robots_fetch_failed')
    text = raw.decode('utf-8-sig')
    active = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    allowed = {'user-agent', 'allow', 'disallow', 'sitemap', 'crawl-delay', 'request-rate', 'host', 'content-signal'}
    for line in filter(None, active):
        if ':' not in line or line.partition(':')[0].lower().strip() not in allowed or '<' in line or '\x00' in line:
            raise RuntimeError('image_robots_unrecognized')
    rules.parse(text.splitlines())
    rate = rules.request_rate(transport.AGENT)
    interval = max(2, rules.crawl_delay(transport.AGENT) or 0, rate.seconds/rate.requests if rate else 0)
    if interval > 60: raise RuntimeError('image_robots_timing_requires_review')
    return rules, interval


def observed_urls(database):
    from booklist.cover_cache import source_url
    with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True, timeout=5) as db:
        values = []
        for first, second in db.execute('select wenku8_cover_url,cover_url_override from booklist_work order by public_added_at is null,id'):
            values.extend((first, second))
        values += [r[0] for r in db.execute('select snapshot_cover_url from booklist_activitywork order by id')]
    return list(dict.fromkeys(url for value in values if (url := source_url(value))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--follow-queue', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    data = args.data_dir.resolve(); checkpoint = args.state.resolve()
    runtime = (ROOT/'.runtime').resolve()
    if data.parent != runtime or not (data/'booklist.sqlite3').is_file() or runtime not in checkpoint.parents:
        parser.error('Only an existing local acceptance database and runtime checkpoint are allowed')
    queue = args.follow_queue.resolve() if args.follow_queue else None
    if queue and (runtime not in queue.parents or not queue.is_file()): parser.error('Invalid detail queue')
    os.environ.update(BOOKLIST_ENV='development', BOOKLIST_DATA_DIR=str(data), DJANGO_SETTINGS_MODULE='config.settings')
    import django
    django.setup()
    from booklist import cover_cache
    from booklist.collecting import atomic_json, collection_lock
    fingerprint = hashlib.sha256(Path(__file__).read_bytes()+Path(cover_cache.__file__).read_bytes()).hexdigest()
    with collection_lock(data/'covers'):
        if args.resume:
            state = json.loads(checkpoint.read_text())
            if state['fingerprint'] != fingerprint or state['data_dir'] != str(data) or state['follow_queue'] != str(queue):
                raise RuntimeError('cover_worker_or_scope_changed')
        else:
            if checkpoint.exists(): raise RuntimeError('cover_checkpoint_exists_use_resume')
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            state = {'version': 1, 'fingerprint': fingerprint, 'data_dir': str(data), 'follow_queue': str(queue),
                'created_at': stamp(), 'items': {}, 'scope': 'Observed source cover URLs only; no text, source cookies or production DB writes'}
        state.update(status='running', worker_pid=os.getpid(), started_at=stamp())
        def save():
            state['updated_at'] = stamp()
            successful = [i for i in state['items'].values() if i['status'] == 'downloaded']
            state['downloaded'] = len(successful)
            state['bytes'] = sum(i['bytes'] for i in successful)
            state['failed'] = sum(i['status'] == 'failed' for i in state['items'].values())
            atomic_json(checkpoint, state)
        save()
        opener = build_opener(transport.NoRedirect())
        opener.addheaders = [('Referer', 'https://www.wenku8.net/')]
        transport.AGENT = 'SharedBooklist-CoverCache/0.1'
        try:
            record, raw, attempts = fetch('https://img.wenku8.com/robots.txt', transport.ROBOTS_LIMIT, opener, state, save, 2)
            state['robots_requests'] = attempts
            rules, interval = robots_policy(record, raw)
            state['interval_seconds'] = interval; save()
            consecutive = 0
            while True:
                urls = observed_urls(data/'booklist.sqlite3')
                state['observed_covers'] = len(urls)
                pending = [url for url in urls if cover_cache.filename(url) not in state['items'] or (
                    state['items'][cover_cache.filename(url)]['status'] == 'downloaded' and not cover_cache.cache_info(cover_cache.filename(url)))]
                state['pending'] = len(pending); save()
                for url in pending:
                    name = cover_cache.filename(url)
                    if not rules.can_fetch(transport.AGENT, url): raise RuntimeError('image_robots_disallows_cover')
                    info = cover_cache.cache_info(name)
                    if info is None:
                        record, raw, attempts = fetch(url, cover_cache.MAX_BYTES, opener, state, save, interval)
                        item = {'source_url': url, 'attempts': attempts, 'finished_at': stamp()}
                        status = record.get('status')
                        if status in (401, 403, 429) or isinstance(status, int) and 300 <= status < 400:
                            state['items'][name] = {**item, 'status': 'failed', 'error': 'source_restriction'}
                            raise RuntimeError('image_source_restriction')
                        try:
                            if raw is None: raise ValueError('image_fetch_failed')
                            info = cover_cache.store(url, raw, record['media_type'])
                        except ValueError as error:
                            state['items'][name] = {**item, 'status': 'failed', 'error': str(error)}
                            state['pending'] -= 1
                            consecutive += 1; save()
                            if consecutive >= 3: raise RuntimeError('consecutive_image_failures_after_bounded_retries')
                            continue
                    else:
                        item = {'source_url': url, 'attempts': [], 'cache_hit': True, 'finished_at': stamp()}
                    state['items'][name] = {**item, **info, 'status': 'downloaded'}
                    state['pending'] -= 1
                    consecutive = 0; state['status'] = 'running'; save()
                    print(json.dumps({'downloaded': state['downloaded'], 'bytes': state['bytes'], 'cover': name}), flush=True)
                if queue:
                    detail = json.loads(queue.read_text())
                    state['detail_queue_status'] = detail['status']
                    if detail['status'] not in ('completed', 'completed_with_errors', 'needs_attention'):
                        state['status'] = 'waiting_metadata'; save(); time.sleep(30); continue
                    # The last metadata batch may have committed during this pass.
                    if any(cover_cache.filename(url) not in state['items'] for url in observed_urls(data/'booklist.sqlite3')):
                        continue
                state['status'] = 'completed_with_errors' if state['failed'] else 'completed'
                state['completed_at'] = stamp(); save(); return 0
        except Exception as error:
            state['status'] = 'needs_attention'; state['error_class'] = type(error).__name__
            state['reason'] = str(error) if isinstance(error, RuntimeError) else 'local_error_requires_review'
            state['stopped_at'] = stamp(); save()
            print(json.dumps({'status': state['status'], 'reason': state['reason']}), flush=True)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
