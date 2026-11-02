"""Optional identity virtual host on the existing public HTTPS edge."""
import json
from pathlib import Path
from scripts.prepare_https import nginx_config

DOMAIN = 'account.sharedbooklist.xyz'
MARKER = Path('/etc/shared-booklist-identity/public-domain.json')


def mode():
    if MARKER.is_symlink():
        raise ValueError('Linked identity public configuration')
    if not MARKER.exists():
        return None
    value = json.loads(MARKER.read_text())
    if value not in ({'domain': DOMAIN, 'mode': 'challenge'}, {'domain': DOMAIN, 'mode': 'enabled'}):
        raise ValueError('Unexpected identity public configuration')
    return value['mode']


def extend(overlay, nginx, state):
    if state not in (None, 'challenge', 'enabled'):
        raise ValueError('Invalid identity ingress state')
    if state is None:
        return overlay, nginx
    proxy = overlay['services']['proxy']
    proxy['volumes'].append('/var/lib/shared-booklist/acme-webroot:/srv/acme:ro')
    source = nginx_config(DOMAIN, state == 'enabled')
    start = source.index('  server { listen 8080; server_name ' + DOMAIN)
    addition = source[start:source.rfind('}')]
    if state == 'enabled':
        # Both Compose projects name their application service "app". Once the
        # edge joins both networks, use exact addresses to prevent alias mixing.
        nginx = nginx.replace('proxy_pass http://app:8000;', 'proxy_pass http://172.30.29.3:8000;')
        overlay['networks']['identity_edge'] = {'external': True, 'name': 'shared-booklist-identity_edge'}
        proxy['networks']['identity_edge'] = {'ipv4_address': '172.30.30.4'}
        proxy['volumes'] += [
            '/etc/shared-booklist-identity/public-tls:/etc/nginx/identity-tls:ro',
            '/var/lib/shared-booklist-identity/static:/srv/identity-static:ro',
        ]
        addition = addition.replace('listen 8443 ssl default_server;', 'listen 8443 ssl;')
        addition = addition.replace('/etc/nginx/tls/', '/etc/nginx/identity-tls/')
        addition = addition.replace('/srv/static/', '/srv/identity-static/public-current/')
        addition = addition.replace('http://app:8000', 'http://172.30.30.3:8000')
    return overlay, nginx[:nginx.rfind('}')] + addition + '}\n'
