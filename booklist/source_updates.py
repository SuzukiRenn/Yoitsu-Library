"""Bounded, single-process source refresh; never runs inside a web request."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from urllib.request import build_opener

from django.conf import settings
from django.db import transaction

from integrations.wenku8.offline import extract, SnapshotError
from integrations.wenku8.session import Client, SourceStop
from integrations.wenku8.updates import discover
from scripts import probe_wenku8 as transport
from scripts.cache_wenku8_covers import fetch as cover_fetch, robots_policy
from . import cover_cache, services
from .collecting import atomic_json, collection_lock
from .importing import validate_snapshot
from .source_review import import_with_review


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe_reason(error):
    value = str(error)
    return value if isinstance(error, (SourceStop, SnapshotError, ValueError, RuntimeError)) and re.fullmatch('[a-z][a-z0-9_]{0,100}', value) else type(error).__name__


def prepare_snapshot(raw, row):
    snapshot = extract(raw, row['book_id'], row['source_url'])
    fields, warnings = validate_snapshot(snapshot)
    # Automatic writes need complete identity/date evidence. Optional errors are
    # kept as diagnostics by the importer, never turned into empty old fields.
    if not snapshot.get('document_diagnostics', {}).get('complete'):
        raise SourceStop('source_detail_incomplete')
    if fields['last_updated']['state'] != 'value' or fields['last_updated']['value'] != row['date']:
        raise SourceStop('source_detail_listing_date_mismatch')
    if fields['cover_url']['state'] not in {'value', 'empty'}:
        raise SourceStop('source_cover_metadata_requires_review')
    return snapshot, fields, warnings


@transaction.atomic
def commit(snapshot, cover):
    work, warnings, created = import_with_review(snapshot)
    if cover:
        work.source_metadata = {**work.source_metadata, 'cover_cache_version': cover}
        work.save(update_fields=['source_metadata', 'updated_at'])
        services.audit(None, 'source.cover.refresh', work, after={'sha256': cover['sha256'], 'name': cover['name']})
    return {'work_id': work.pk, 'created': created, 'warnings': warnings,
            'pending_identity_review': work.review_status == 'pending'}


class Images:
    """Separate, unauthenticated image transport: source cookies never cross hosts."""
    def __init__(self, save, state):
        self.state, self.save = state, save
        self.opener = build_opener(transport.NoRedirect())
        self.opener.addheaders = [('Referer', transport.BASE+'/')]
        self.rules = None
        self.interval = 2

    def request(self, url, limit):
        record, raw, _ = cover_fetch(url, limit, self.opener, self.state, self.save, self.interval)
        code = record.get('status')
        if code in {401, 403, 429} or isinstance(code, int) and 300 <= code < 400:
            raise SourceStop('image_source_restriction')
        return record, raw

    def get(self, value):
        url = cover_cache.source_url(value)
        if url is None:
            raise SourceStop('cover_origin_not_allowed')
        if self.rules is None:
            record, raw = self.request('https://img.wenku8.com/robots.txt', transport.ROBOTS_LIMIT)
            self.rules, self.interval = robots_policy(record, raw)
        if not self.rules.can_fetch(transport.AGENT, url):
            raise SourceStop('image_robots_disallows_cover')
        record, raw = self.request(url, cover_cache.MAX_BYTES)
        if raw is None:
            raise ValueError('image_fetch_failed')
        return cover_cache.store_version(url, raw, record.get('media_type'))


def run(credentials, *, dry_run=False):
    """Resume an interrupted durable run; safety stops require operator handling."""
    directory = Path(settings.DATA_DIR)/'source_updates'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise SourceStop('linked_source_state')
    checkpoint = directory/'daily.json'
    if checkpoint.is_symlink():
        raise SourceStop('linked_source_checkpoint')
    with collection_lock(directory):
        state = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.is_file() else {'version': 1, 'status': 'idle'}
        if state['status'] == 'needs_attention':
            return {'status': 'needs_attention', 'reason': state.get('reason'), 'action': 'operator_review_required'}
        started = time.monotonic()
        client = None
        def save():
            state['updated_at'] = stamp()
            state['requests'] = client.requests if client is not None else []
            atomic_json(checkpoint, state)
        try:
            client = Client(directory, credentials, request_limit=700, seconds_limit=3600)
            client.robots()
            if state['status'] not in {'running', 'discovered'}:
                today = datetime.now(timezone(timedelta(hours=8))).date()
                prior = state.get('last_success_at')
                cutoff = (datetime.fromisoformat(prior).astimezone(timezone(timedelta(hours=8))).date()-timedelta(days=1)
                          if prior else today-timedelta(days=2))
                rows = discover(client, cutoff, today)
                state = {'version': 1, 'status': 'discovered', 'started_at': stamp(),
                         'last_success_at': prior, 'cutoff': cutoff.isoformat(),
                         'items': [{**row, 'status': 'pending'} for row in rows]}
                if dry_run:
                    report = {**state, 'status': 'dry_run', 'requests': client.requests}
                    atomic_json(directory/'dry_run.json', report)
                    return report
                save()
            elif dry_run:
                return {'status': 'dry_run', 'items': state['items'], 'scope': 'existing checkpoint; no imports'}
            state['status'] = 'running'; save()
            images = Images(save, state)
            for item in state['items']:
                if item['status'] != 'pending':
                    continue
                if time.monotonic()-started >= 3600:
                    raise SourceStop('source_run_budget_reached')
                try:
                    record, raw = client.request(item['source_url'])
                    if raw is None or record.get('media_type') != 'text/html':
                        raise ValueError('source_detail_fetch_failed')
                    snapshot, fields, _ = prepare_snapshot(raw, item)
                    cover = images.get(fields['cover_url']['value']) if fields['cover_url']['state'] == 'value' else None
                    item.update(commit(snapshot, cover), status='imported', finished_at=stamp())
                except SourceStop as error:
                    # Format drift is isolated to an item. Access, robots and
                    # runtime budget stops halt all further remote requests.
                    if str(error) not in {'source_detail_incomplete', 'source_detail_listing_date_mismatch',
                                         'source_cover_metadata_requires_review'}:
                        raise
                    item.update(status='failed', reason=safe_reason(error), finished_at=stamp())
                except (SnapshotError, ValueError) as error:
                    item.update(status='failed', reason=safe_reason(error), finished_at=stamp())
                save()
            failed = [item for item in state['items'] if item['status'] == 'failed']
            if failed:
                # Preserve failures outside the rolling list so older unresolved
                # items cannot disappear merely because the date window moves.
                atomic_json(directory/('failures-'+state['started_at'].replace(':', '-')+'.json'), {'items': failed})
            state['status'] = 'completed_with_errors' if failed else 'completed'
            state['last_success_at'] = stamp()
            state['completed_at'] = stamp()
            save()
            atomic_json(directory/('run-'+state['started_at'].replace(':', '-')+'.json'), state)
            return {key: state[key] for key in ('status', 'last_success_at', 'items')}
        except Exception as error:
            state.update(status='needs_attention', reason=safe_reason(error), stopped_at=stamp())
            save()
            return {'status': 'needs_attention', 'reason': state['reason']}
