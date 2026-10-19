"""Discover up to 20 new IDs from at most three explicitly known public details.

No directory/login access, sequential ID guessing, automatic retry or DB import.
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import build_opener
import uuid

ROOT = Path(__file__).absolute().parents[1]


def main():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument('--seed-ids', nargs='+', type=int, required=True)
    command.add_argument('--exclude-ids', nargs='+', type=int, default=[])
    args = command.parse_args()
    if not 1 <= len(args.seed_ids) <= 3 or len(args.exclude_ids) > 1000:
        command.error('Require 1..3 explicit seed IDs and at most 1000 excluded IDs')
    if any(not 1 <= value <= 99999999 for value in args.seed_ids+args.exclude_ids) or len(set(args.seed_ids)) != len(args.seed_ids):
        command.error('Invalid or duplicate seed IDs')
    sys.path.insert(0, str(ROOT))
    out = ROOT/'.runtime/source_discovery'/(
        datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    os.environ.update(BOOKLIST_ENV='test', BOOKLIST_DATA_DIR=str(out/'unused_data'), DJANGO_SETTINGS_MODULE='config.settings')
    import django
    django.setup()
    from booklist.collecting import atomic_json, collection_lock, discover_ids, MIN_INTERVAL
    from integrations.wenku8.offline import extract, parser, SnapshotError
    from scripts import probe_wenku8 as source
    result = {'success': False, 'seed_ids': args.seed_ids, 'exclude_ids': args.exclude_ids,
              'book_ids': [], 'requests': [], 'stop_reason': '',
              'scope': 'bounded public detail links only; not a complete catalog; no DB import or page persistence'}
    interval = MIN_INTERVAL; opener = build_opener(source.NoRedirect())
    def save(): atomic_json(out/'summary.json', result)
    def fetch(url, limit):
        time.sleep(interval)
        attempt = {'url': url, 'result': 'started'}
        result['requests'].append(attempt); save()
        record, raw = source.fetch(url, limit, opener)
        attempt.update(record); save()
        return record, raw
    try:
        parser()
        with collection_lock(ROOT/'.runtime/source_collections'):
            record, raw = fetch(source.BASE+'/robots.txt', source.ROBOTS_LIMIT)
            if raw is None:
                raise SnapshotError('robots_fetch_failed')
            policy, rules = source.policy(raw, record['media_type'])
            result['robots'] = policy
            if rules is None or policy['minimum_interval_seconds'] > 60:
                raise SnapshotError('robots_unavailable_or_timing_exceeds_budget')
            interval = max(MIN_INTERVAL, policy['minimum_interval_seconds'])
            for book_id in args.seed_ids:
                url = source.BASE+f'/book/{book_id}.htm'
                if not rules.can_fetch(source.AGENT, url):
                    raise SnapshotError('robots_disallows_seed')
                record, raw = fetch(url, source.DETAIL_LIMIT)
                if raw is None or record['media_type'] != 'text/html':
                    raise SnapshotError('seed_fetch_failed')
                snapshot = extract(raw, book_id, url)
                if not snapshot['document_diagnostics']['complete']:
                    raise SnapshotError('seed_document_incomplete')
                try:
                    ids = discover_ids(raw, url, set(args.exclude_ids+args.seed_ids+result['book_ids']))
                except SnapshotError as exc:
                    if str(exc) != 'listing_has_no_detail_links': raise
                    ids = []
                result['book_ids'].extend(ids[:20-len(result['book_ids'])]); save()
                print(json.dumps({'seed_id': book_id, 'new_ids_so_far': len(result['book_ids'])}), flush=True)
                if len(result['book_ids']) == 20:
                    break
            result['success'] = bool(result['book_ids'])
            if not result['success']: result['stop_reason'] = 'no_new_public_links'
    except SnapshotError as exc:
        result['stop_reason'] = str(exc)
    except Exception as exc:
        result['error_class'] = type(exc).__name__
    save()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print('EVIDENCE:', out)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
