"""Render local-only HTTPS configuration. Never connects, deploys, or issues certificates."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parent.parent
NGINX_IMAGE = 'nginx:1.30.4-alpine@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c'
CERTBOT_IMAGE = 'certbot/certbot:v5.8.0@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20'


def validate(domain, release, subnet, proxy_ip):
    domain = domain.lower()
    labels = domain.split('.')
    if (len(domain) > 253 or len(labels) < 2 or labels[-1].isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('Domain must be an ASCII hostname, without scheme, path, or wildcard')
    if not re.fullmatch(r'[0-9a-f]{40}', release):
        raise ValueError('Release must be a full lowercase commit SHA')
    network = ipaddress.IPv4Network(subnet, strict=True)
    address = ipaddress.IPv4Address(proxy_ip)
    if (not network.is_private or not network.subnet_of(ipaddress.IPv4Network('172.16.0.0/12'))
            or not 24 <= network.prefixlen <= 28 or address not in network
            or int(address) <= int(network.network_address) + 1 or address == network.broadcast_address):
        raise ValueError('Use an unused /24 through /28 subnet in 172.16.0.0/12; reserve gateway .1')
    return domain, str(network), str(address)


def nginx_config(domain, tls):
    header = '''worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 256; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  access_log off;
  server_tokens off;
  client_max_body_size 32k;
  client_body_temp_path /tmp/client_body;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server { listen 8080 default_server; server_name _; return 444; }
'''
    challenge = '''    location ^~ /.well-known/acme-challenge/ {
      root /srv/acme; default_type text/plain; try_files $uri =404;
    }
'''
    http = f'  server {{ listen 8080; server_name {domain};\n' + challenge
    http += (f'    location / {{ return 308 https://{domain}$request_uri; }}\n' if tls
             else '    location / { return 404; }\n')
    http += '  }\n'
    if not tls:
        return header + http + '}\n'
    https = f'''  server {{
    listen 8443 ssl default_server;
    server_name {domain};
    if ($host != {domain}) {{ return 444; }}
    ssl_certificate /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    location = /static/favicon.svg {{
      alias /srv/static/favicon.svg;
      default_type image/svg+xml;
      etag off;
      if_modified_since off;
      add_header Cache-Control "no-store" always;
      add_header X-Content-Type-Options nosniff always;
    }}
    location /static/ {{
      alias /srv/static/;
      types {{ text/css css; text/javascript js mjs; image/svg+xml svg; image/png png; }}
      autoindex off;
      add_header X-Content-Type-Options nosniff always;
    }}
    location / {{
      proxy_pass http://app:8000;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_set_header Upgrade "";
      proxy_set_header Host {domain};
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header Forwarded "";
      proxy_set_header X-Forwarded-Host "";
      proxy_set_header X-Real-IP "";
      proxy_connect_timeout 5s;
      proxy_read_timeout 30s;
    }}
  }}
}}
'''
    return header + http + https


def render(domain, release, subnet='172.30.29.0/28', proxy_ip='172.30.29.2'):
    domain, subnet, proxy_ip = validate(domain, release, subnet, proxy_ip)
    hardening = {'read_only': True, 'cap_drop': ['ALL'],
                 'security_opt': ['no-new-privileges:true'], 'platform': 'linux/amd64',
                 'logging': {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '2'}}}
    overlay = {
        'services': {
            'app': {'environment': {'BOOKLIST_ALLOWED_HOSTS': domain + ',localhost,127.0.0.1',
                                    'BOOKLIST_TRUSTED_PROXY_IPS': proxy_ip},
                    'networks': {'private': {}, 'edge_backend': {}}},
            'proxy': {**hardening, 'image': NGINX_IMAGE, 'user': '10001:10001',
                      'entrypoint': ['nginx'], 'command': ['-g', 'daemon off;'],
                      'restart': 'unless-stopped', 'mem_limit': '128m', 'cpus': 0.25,
                      'tmpfs': ['/tmp:size=64m,mode=1777'],
                      'ports': ['127.0.0.1:8080:8080', '127.0.0.1:8443:8443'],
                      'depends_on': {'app': {'condition': 'service_healthy'}},
                      'networks': {'edge_backend': {'ipv4_address': proxy_ip}, 'edge_front': {}},
                      'volumes': [
                          '/etc/shared-booklist/https/nginx.conf:/etc/nginx/nginx.conf:ro',
                          '/etc/shared-booklist/https/tls:/etc/nginx/tls:ro',
                          f'/var/lib/shared-booklist/predeploy/static/{release}:/srv/static:ro',
                          '/var/lib/shared-booklist/acme-webroot:/srv/acme:ro']},
            'certbot': {**hardening, 'image': CERTBOT_IMAGE, 'profiles': ['certificates'],
                        'command': ['--version'], 'networks': {'edge_front': {}},
                        'tmpfs': ['/tmp:size=16m,mode=1777', '/var/log/letsencrypt:size=16m,mode=1777',
                                  '/var/lib/letsencrypt:size=16m,mode=1777'],
                        'volumes': ['/etc/shared-booklist/acme:/etc/letsencrypt',
                                    '/var/lib/shared-booklist/acme-webroot:/srv/acme']},
        },
        'networks': {'edge_backend': {'internal': True, 'ipam': {'config': [{'subnet': subnet}]}},
                     'edge_front': {}},
    }
    manifest = {'domain': domain, 'release': release, 'proxy_ip': proxy_ip, 'subnet': subnet,
                'state': 'local_preparation_only', 'public_access': False,
                'certificate_issued': False, 'server_modified': False,
                'prerequisites': ['domain ownership and real-name verification', 'ICP approval',
                                  'server subnet collision check', 'Linux nginx and TLS acceptance',
                                  'certificate issuance and renewal drill']}
    return {'compose.https.json': json.dumps(overlay, ensure_ascii=False, indent=2) + '\n',
            'nginx.bootstrap.conf': nginx_config(domain, False),
            'nginx.https.conf': nginx_config(domain, True),
            'manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--subnet', default='172.30.29.0/28')
    parser.add_argument('--proxy-ip', default='172.30.29.2')
    args = parser.parse_args()
    try:
        files = render(args.domain, args.release, args.subnet, args.proxy_ip)
    except ValueError as error:
        parser.error(str(error))
    output = ROOT / '.runtime' / 'https_preparation' / uuid.uuid4().hex
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (output / name).write_text(content, encoding='utf-8', newline='\n')
    print(output)


if __name__ == '__main__':
    main()
