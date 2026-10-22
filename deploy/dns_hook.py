"""Certbot AliDNS hook. Fixed domain; deletes only record IDs created by this hook."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import uuid

DOMAIN = 'sharedbooklist.xyz'
STATE = Path('/var/lib/booklist-dns')
CREDENTIALS = Path('/run/booklist-dns.json')


def signed_parameters(action, values, credentials):
    if action not in {'AddDomainRecord', 'DeleteDomainRecord'}:
        raise ValueError('unsupported_action')
    params = dict(values, Action=action, Version='2015-01-09', Format='JSON',
                  AccessKeyId=credentials['access_key_id'], SignatureMethod='HMAC-SHA1',
                  SignatureVersion='1.0', SignatureNonce=uuid.uuid4().hex,
                  Timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    encode = lambda value: quote(str(value), safe='~')
    canonical = '&'.join(encode(k)+'='+encode(v) for k, v in sorted(params.items()))
    signature = hmac.new((credentials['access_key_secret']+'&').encode(),
                         ('POST&%2F&'+encode(canonical)).encode(), hashlib.sha1).digest()
    params['Signature'] = base64.b64encode(signature).decode()
    return params


def api(action, **values):
    credentials = json.loads(CREDENTIALS.read_text())
    request = Request('https://alidns.aliyuncs.com/',
                      data=urlencode(signed_parameters(action, values, credentials)).encode())
    # Never log signed URLs, request bodies, credentials, or provider error messages.
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError):
        raise RuntimeError('dns_api_request_failed') from None
    if 'Code' in result:
        raise RuntimeError('dns_api_rejected')
    return result


def challenge(environment):
    if environment.get('CERTBOT_DOMAIN') != DOMAIN:
        raise ValueError('unexpected_domain')
    token = environment.get('CERTBOT_VALIDATION', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{30,200}', token):
        raise ValueError('invalid_challenge')
    return token, hashlib.sha256(token.encode()).hexdigest()


def hook(mode, environment):
    token, digest = challenge(environment)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = STATE / (digest+'.json')
    if mode == 'auth':
        if path.exists():
            raise RuntimeError('existing_challenge_requires_review')
        result = api('AddDomainRecord', DomainName=DOMAIN, RR='_acme-challenge',
                     Type='TXT', Value=token, TTL='600')
        record = str(result['RecordId'])
        if not re.fullmatch(r'[0-9]+', record):
            raise RuntimeError('unexpected_record_id')
        # Persist ownership before waiting. A crashed run can be investigated safely.
        with path.open('x') as stream:
            os.chmod(path, 0o600)
            json.dump({'domain': DOMAIN, 'digest': digest, 'record_id': record}, stream)
            stream.flush(); os.fsync(stream.fileno())
        time.sleep(120)  # Actual propagation is verified by the ACME staging drill.
        print(record, flush=True)
    elif mode == 'cleanup':
        owned = json.loads(path.read_text())
        if owned != {'domain': DOMAIN, 'digest': digest,
                     'record_id': environment.get('CERTBOT_AUTH_OUTPUT', '').strip()}:
            raise RuntimeError('record_ownership_mismatch')
        api('DeleteDomainRecord', RecordId=owned['record_id'])
        path.unlink()
    else:
        raise ValueError('unsupported_mode')


if __name__ == '__main__':
    try:
        hook(sys.argv[1], os.environ)
    except Exception:
        print('DNS hook failed; inspect private state and RAM permissions.', file=sys.stderr)
        sys.exit(1)
