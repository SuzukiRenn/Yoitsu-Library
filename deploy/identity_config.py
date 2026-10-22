"""Independent identity Compose configuration; never points to the anonymous data."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prepare_https import NGINX_IMAGE, nginx_config

ROOT = Path('/opt/shared-booklist-identity')
CONFIG = Path('/etc/shared-booklist-identity')
STATE = Path('/var/lib/shared-booklist-identity')
PROJECT = 'shared-booklist-identity'
DOMAIN = 'sharedbooklist.xyz'
SUBNET = '172.30.30.0/28'
PROXY = '172.30.30.2'
PORT = 8444
# Read-only existing certificate directory; never replace or modify it.
TLS = '/etc/shared-booklist/ssh-access/tls'


def configuration(release):
    posix = lambda path: path.as_posix()
    common = {'image': 'shared-booklist-identity:' + release,
              'env_file': [posix(CONFIG / 'production.env')], 'user': '10001:10001',
              'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
              'tmpfs': ['/tmp:size=32m,mode=1777'], 'restart': 'unless-stopped',
              'volumes': [posix(STATE / 'data') + ':/var/lib/shared-booklist-identity'],
              'networks': ['private'], 'mem_limit': '128m', 'cpus': 0.25,
              'logging': {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '2'}}}
    app = {**common, 'mem_limit': '256m', 'cpus': 0.75,
           'networks': {'private': {}, 'edge': {'ipv4_address': '172.30.30.3'}},
           'healthcheck': {'test': ['CMD', 'python', 'scripts/predeploy_health.py'],
                           'interval': '15s', 'timeout': '10s', 'retries': 4, 'start_period': '15s'}}
    tools = {**common, 'mem_limit': '256m', 'restart': 'no', 'profiles': ['tools']}
    scheduler = {**common, 'command': ['python', 'scripts/production_tasks.py', 'scheduler'],
                 'depends_on': {'app': {'condition': 'service_healthy'}}}
    backup = {**common, 'command': ['python', 'scripts/production_tasks.py', 'backup'],
              'volumes': [posix(STATE / 'data') + ':/var/lib/shared-booklist-identity',
                          posix(STATE / 'backups') + ':/backups'],
              'depends_on': {'app': {'condition': 'service_healthy'}}}
    proxy = {key: value for key, value in common.items() if key not in ('env_file', 'volumes', 'networks')}
    proxy.update(image=NGINX_IMAGE, entrypoint=['nginx'], command=['-g', 'daemon off;'],
                 ports=[f'127.0.0.1:{PORT}:8443'], networks={'edge': {'ipv4_address': PROXY}, 'front': {}},
                 volumes=[posix(CONFIG / ('nginx-' + release + '.conf')) + ':/etc/nginx/nginx.conf:ro',
                          TLS + ':/etc/nginx/tls:ro', posix(STATE / 'static' / release) + ':/srv/static:ro'],
                 depends_on={'app': {'condition': 'service_healthy'}})
    compose = {'name': PROJECT, 'services': {'app': app, 'scheduler': scheduler, 'backup': backup, 'proxy': proxy, 'tools': tools},
               'networks': {'private': {'internal': True},
                            'edge': {'internal': True, 'ipam': {'config': [{'subnet': SUBNET}]}}, 'front': {}}}
    nginx = nginx_config(DOMAIN, True).replace('proxy_set_header Host ' + DOMAIN + ';', 'proxy_set_header Host $http_host;')
    return compose, nginx
