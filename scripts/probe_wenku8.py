"""Bounded, unauthenticated route check; stdlib only, no database writes.

At most two explicit GET attempts. No redirects, retries, browser sessions,
cover downloads, novel text, or response-body persistence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import platform
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser


BASE = 'https://www.wenku8.net'
AGENT = 'SharedBooklist-MetadataCheck/0.2'
ROBOTS_LIMIT = 64 * 1024
DETAIL_LIMIT = 512 * 1024
REQUEST_BUDGET_SECONDS = 30


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, limit, opener, clock=time.monotonic):
    started = clock()
    record = {'url': url, 'status': None, 'result': 'transport_failed',
              'phase': 'connect_or_headers', 'bytes_read': 0, 'chunks_read': 0,
              'body_complete': False}
    request = Request(url, headers={'User-Agent': AGENT, 'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=15) as response:
            record.update(status=response.status, phase='headers', headers_ms=round((clock()-started)*1000, 3),
                          media_type=response.headers.get_content_type())
            if response.status != 200:
                record['result'] = 'http_status_stop'
                return record, None
            encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
            if encoding not in ('', 'identity'):
                record['result'] = 'unsupported_content_encoding'
                return record, None
            lengths = response.headers.get_all('Content-Length', [])
            if lengths and (len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,12}', lengths[0].strip())):
                record['result'] = 'invalid_content_length'
                return record, None
            declared = int(lengths[0]) if lengths else None
            record.update(declared_length=declared,
                          chunked='chunked' in response.headers.get('Transfer-Encoding', '').lower(),
                          phase='body')
            if declared is not None and declared > limit:
                record['result'] = 'response_too_large'
                return record, None
            chunks = []
            while True:
                # read1 returns available bytes rather than waiting to fill the
                # entire cap. Keep progress when a later socket operation fails.
                # The wall-clock budget is checked between bounded I/O calls.
                if clock()-started > REQUEST_BUDGET_SECONDS:
                    record['result'] = 'request_budget_exceeded'
                    return record, None
                chunk = response.read1(min(4096, limit+1-record['bytes_read']))
                if not chunk:
                    break
                if not chunks:
                    record['first_byte_ms'] = round((clock()-started)*1000, 3)
                chunks.append(chunk)
                record['bytes_read'] += len(chunk)
                record['chunks_read'] += 1
                if record['bytes_read'] > limit:
                    record['result'] = 'response_too_large'
                    return record, None
            if clock()-started > REQUEST_BUDGET_SECONDS:
                record['result'] = 'request_budget_exceeded'
                return record, None
            if declared is not None and record['bytes_read'] != declared:
                record['result'] = 'content_length_mismatch'
                return record, None
            raw = b''.join(chunks)
            record.update(body_complete=True, phase='complete')
            record.update(result='received', sha256=hashlib.sha256(raw).hexdigest(),
                          media_type=response.headers.get_content_type())
            return record, raw
    except HTTPError as exc:
        record.update(status=exc.code, result='http_status_stop')
        exc.close()
    except (URLError, OSError, ValueError, HTTPException) as exc:
        # Do not emit exception text: it may contain proxy addresses or credentials.
        record['error_class'] = type(exc).__name__
    finally:
        record['elapsed_ms'] = round((clock()-started)*1000, 3)
    return record, None


def policy(raw, media_type):
    if media_type != 'text/plain':
        return {'result': 'robots_unrecognized'}, None
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        return {'result': 'robots_unrecognized'}, None
    if '<' in text or '\x00' in text:
        return {'result': 'robots_unrecognized'}, None
    lines = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    active = [line for line in lines if line]
    allowed_keys = {'user-agent', 'allow', 'disallow', 'sitemap', 'crawl-delay', 'request-rate', 'host'}
    for line in active:
        key, sep, value = line.partition(':')
        if not sep or key.lower().strip() not in allowed_keys:
            return {'result': 'robots_unrecognized'}, None
        if key.lower().strip() == 'crawl-delay' and not re.fullmatch(r'\d+', value.strip()):
            return {'result': 'robots_unsupported_timing'}, None
        if key.lower().strip() == 'request-rate' and not re.fullmatch(r'[1-9]\d*/[1-9]\d*', value.strip()):
            return {'result': 'robots_unsupported_timing'}, None
    parser = RobotFileParser(BASE + '/robots.txt')
    parser.parse(lines)
    delay = parser.crawl_delay(AGENT)
    rate = parser.request_rate(AGENT)
    interval = max(10, delay or 0, rate.seconds / rate.requests if rate else 0)
    return {'result': 'parsed', 'minimum_interval_seconds': interval}, parser


def probe(location, opener=None, sleeper=time.sleep, metadata_validator=None):
    # The default opener has no cookie processor, auth handlers or cookie source.
    if opener is None:
        opener = build_opener(NoRedirect())
    result = {'schema_version': 2, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'location': location, 'environment': {'os': platform.system(), 'architecture': platform.machine(),
                  'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version},
              'authenticated': False, 'redirects_followed': False, 'automatic_retry': False,
              'maximum_get_attempts': 2, 'requests': [], 'outcome': 'stopped',
              'scope': 'network, robots and optional identity diagnostics; no optional-value validation, DB import, production readiness or bulk permission'}
    record, raw = fetch(BASE + '/robots.txt', ROBOTS_LIMIT, opener)
    result['requests'].append(record)
    if raw is None:
        result['stop_reason'] = 'robots_fetch_failed'
        return result
    rules, parser = policy(raw, record['media_type'])
    result['robots'] = rules
    if parser is None:
        result['stop_reason'] = rules['result']
        return result
    detail = BASE + '/book/4151.htm'
    allowed = parser.can_fetch(AGENT, detail)
    result['robots']['detail_allowed'] = allowed
    if not allowed:
        result['stop_reason'] = 'robots_disallows_detail'
        return result
    interval = rules['minimum_interval_seconds']
    if interval > 60:
        result['stop_reason'] = 'robots_timing_requires_manual_schedule'
        return result
    sleeper(interval)
    record, raw = fetch(detail, DETAIL_LIMIT, opener)
    result['requests'].append(record)
    if raw is None:
        result['stop_reason'] = 'detail_fetch_failed'
    elif record['media_type'] != 'text/html':
        result['stop_reason'] = 'detail_unrecognized_media_type'
    else:
        result['outcome'] = 'detail_http_received_metadata_unverified'
        if metadata_validator is not None:
            try:
                result['metadata'] = metadata_validator(raw)
                result['outcome'] = 'detail_identity_verified_optional_fields_unvalidated'
            except Exception as exc:
                result['metadata'] = {'result': 'failed', 'error_class': type(exc).__name__}
                result['outcome'] = 'stopped'
                result['stop_reason'] = 'metadata_check_failed'
    return result


def check_metadata(raw):
    # Local opt-in only. Reuse the existing adapter; do not add a second parser.
    from integrations.wenku8.offline import extract
    snapshot = extract(raw, 4151, BASE + '/book/4151.htm')
    fields = snapshot['fields']
    if fields['title']['state'] != 'value':
        raise ValueError('Invalid standard title')
    return {'result': 'identity_verified', 'book_id': snapshot['book_id'],
            'document_diagnostics': snapshot['document_diagnostics'],
            'title': fields['title']['value'],
            'field_states': {name: value['state'] for name, value in fields.items()},
            'diagnostic_codes': {name: value['code'] for name, value in fields.items() if value['code']},
            'tags_complete': fields['tags']['complete'],
            'scope': 'adapter identity and field diagnostics; optional-value validation and database import not performed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--location', required=True, choices=('local', 'server'))
    parser.add_argument('--check-metadata', action='store_true', help='Local installed adapter only; no DB writes')
    args = parser.parse_args()
    if args.check_metadata:
        if args.location != 'local':
            parser.error('--check-metadata is only supported for the local installed adapter')
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
        # Fail before any HTTP request if local parser dependencies are absent.
        from integrations.wenku8.offline import parser as load_parser
        load_parser()
    result = probe(args.location, metadata_validator=check_metadata if args.check_metadata else None)
    # ASCII JSON survives PowerShell/native stdout encoding differences; JSON
    # decoders recover Unicode without changing the parsed title.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['outcome'] in ('detail_http_received_metadata_unverified',
                                     'detail_identity_verified_optional_fields_unvalidated') else 1


if __name__ == '__main__':
    raise SystemExit(main())
