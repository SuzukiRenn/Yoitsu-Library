"""Collect at most 20 detail metadata records into a dedicated local catalog.

No production/dev database, source login, cover files, novel text, or scheduled job.
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).absolute().parents[1]
OUTPUT_ROOT = ROOT/'.runtime/source_collections'


def main():
    args_parser = argparse.ArgumentParser(description=__doc__)
    group = args_parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ids', type=int, nargs='+')
    group.add_argument('--discover-url', help='An observed Wenku8 listing URL; one page only, max 20 IDs')
    group.add_argument('--resume', type=Path)
    args_parser.add_argument('--max-items', type=int, default=20)
    args_parser.add_argument('--skip-ids', type=int, nargs='+', help='Exclude up to 20 previously collected IDs from one discovery page')
    args_parser.add_argument('--retry-failed', action='store_true')
    args_parser.add_argument('--network-retries', type=int, choices=range(4), default=0,
        help='Opt-in transient transport retries per request (0..3), with 30/60/120-second backoff')
    args_parser.add_argument('--refresh', action='store_true', help='Explicitly refresh a fully completed batch in its existing catalog')
    args_parser.add_argument('--shared-pacing', type=Path, help='Coordinator-owned shared pacing checkpoint')
    args = args_parser.parse_args()
    if not 1 <= args.max_items <= 20 or (args.retry_failed and not args.resume):
        args_parser.error('max-items must be 1..20; retry-failed requires resume')
    if args.refresh and (not args.resume or args.retry_failed):
        args_parser.error('refresh requires resume and cannot combine with retry-failed')
    if args.skip_ids and not args.discover_url:
        args_parser.error('skip-ids requires discover-url')
    sys.path.insert(0, str(ROOT))
    # Resolve and constrain output before configuring Django. Never inherit an existing data directory.
    directory = args.resume.absolute().resolve() if args.resume else OUTPUT_ROOT/(
        datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    if directory.parent != OUTPUT_ROOT.resolve() or directory.is_symlink():
        args_parser.error('resume must name one immediate batch directory under .runtime/source_collections')
    if args.resume and not (directory/'state.json').is_file():
        args_parser.error('resume checkpoint not found')
    if (directory/'data').exists() and (directory/'data').resolve().parent != directory:
        args_parser.error('linked data directory is not allowed')
    if not args.resume:
        directory.mkdir(parents=True, exist_ok=False)
    os.environ.update(BOOKLIST_ENV='test', BOOKLIST_DATA_DIR=str(directory/'data'), DJANGO_SETTINGS_MODULE='config.settings')
    import django
    django.setup()
    from django.core.management import call_command
    from booklist.collecting import collection_lock, new_state, validate_state, atomic_json, run
    result = {'success': False}
    pacing = args.shared_pacing.resolve() if args.shared_pacing else None
    expected_pacing = (ROOT/'.runtime/directory_detail_queues/retry_220_20261020/pacing.json').resolve()
    if pacing and (pacing != expected_pacing or not pacing.is_file()):
        args_parser.error('shared pacing must be initialized by the frozen queue coordinator')
    try:
        with collection_lock(directory if pacing else OUTPUT_ROOT):
            if args.resume:
                state = json.loads((directory/'state.json').read_text(encoding='utf-8'))
                validate_state(state)
                if args.refresh:
                    if not state['items'] or any(item['status'] not in ('imported','partial') for item in state['items']):
                        raise ValueError('Finish the batch before refreshing')
                    for item in state['items']:
                        item['status'] = 'pending'
                    state['stop_reason'] = ''
                    atomic_json(directory/'state.json', state)
            else:
                state = new_state(args.ids, args.discover_url, args.skip_ids)
                atomic_json(directory/'state.json', state)
            print('BATCH:', directory, flush=True)
            with (directory/'migrate.log').open('a', encoding='utf-8') as log:
                call_command('migrate', interactive=False, stdout=log, verbosity=1)
            opener = None
            if pacing:
                from urllib.request import build_opener
                from scripts import probe_wenku8 as source
                from scripts.probe_wenku8 import NoRedirect
                import time
                from booklist.collection_parallel import PacedOpener
                opener = PacedOpener(build_opener(NoRedirect()), pacing,
                    lambda: state.get('robots', {}).get('minimum_interval_seconds', 10))
                fetch = source.fetch
                # Queue waiting is not network time; preserve the original 30s I/O budget.
                source.fetch = lambda url, limit, transport: fetch(url, limit, transport,
                    clock=lambda: time.monotonic() - opener.wait_seconds)
            result = run(directory, state, opener=opener, max_items=args.max_items, retry_failed=args.retry_failed,
                         network_retries=args.network_retries,
                         progress=lambda item: print(json.dumps(item, ensure_ascii=True), flush=True))
    except KeyboardInterrupt:
        result = {'success': False, 'error_code': 'interrupted_resume_same_batch'}
    except Exception as exc:
        # Do not log exception text, which might include transport/connection credentials.
        result = {'success': False, 'error_class': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print('EVIDENCE:', directory)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
