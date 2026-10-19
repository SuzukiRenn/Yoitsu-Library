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
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser


BASE = 'https://www.wenku8.net'
AGENT = 'SharedBooklist-MetadataCheck/0.2'
ROBOTS_LIMIT = 64 * 1024
DETAIL_LIMIT = 512 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, limit, opener):
    record = {'url': url, 'status': None, 'result': 'transport_failed'}
    request = Request(url, headers={'User-Agent': AGENT, 'Accept-Encoding': 'identity'})
    try:
        with opener.open(request, timeout=15) as response:
            record['status'] = response.status
            if response.status != 200:
                record['result'] = 'http_status_stop'
                return record, None
            encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
            if encoding not in ('', 'identity'):
                record['result'] = 'unsupported_content_encoding'
                return record, None
            raw = response.read(limit + 1)
            record['bytes_read'] = len(raw)
            if len(raw) > limit:
                record['result'] = 'response_too_large'
                return record, None
            record.update(result='received', sha256=hashlib.sha256(raw).hexdigest(),
                          media_type=response.headers.get_content_type())
            return record, raw
    except HTTPError as exc:
        record.update(status=exc.code, result='http_status_stop')
        exc.close()
    except (URLError, OSError, ValueError, HTTPException) as exc:
        # Do not emit exception text: it may contain proxy addresses or credentials.
        record['error_class'] = type(exc).__name__
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


def probe(location, opener=None, sleeper=time.sleep):
    # The default opener has no cookie processor, auth handlers or cookie source.
    if opener is None:
        opener = build_opener(NoRedirect())
    result = {'schema_version': 1, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'location': location, 'environment': {'os': platform.system(), 'architecture': platform.machine(),
                  'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version},
              'authenticated': False, 'redirects_followed': False, 'automatic_retry': False,
              'maximum_get_attempts': 2, 'requests': [], 'outcome': 'stopped',
              'scope': 'network and robots only; not metadata validation, production readiness or permission for bulk collection'}
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
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--location', required=True, choices=('local', 'server'))
    args = parser.parse_args()
    result = probe(args.location)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['outcome'] == 'detail_http_received_metadata_unverified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
