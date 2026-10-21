"""Verify the staged proxy with the deployed image and a disposable database."""
import hashlib
import hmac
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid


def run(*args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    require(os.geteuid() == 0, 'Root required for isolated namespace checks')
    require(sys.argv[2:] in ([], ['--production-certificate']), 'Unexpected certificate mode')
    production_certificate = bool(sys.argv[2:])
    certificate_info = None
    stage = Path(sys.argv[1]).resolve()
    require(stage.parent == Path('/etc/shared-booklist/https-staging'), 'Unexpected staging path')
    manifest = json.loads((stage / 'manifest.json').read_text())
    receipt = json.loads((stage / 'receipt.json').read_text())
    domain, release = manifest['domain'], manifest['release']
    require(receipt['release'] == release and not receipt['active_configuration_changed'],
            'Expected an inactive, verified configuration')
    require(Path('/var/lib/shared-booklist/predeploy/release.txt').read_text().strip() == release,
            'Running release changed')
    compose = json.loads((stage / 'compose.https.json').read_text())
    proxy_ip = manifest['proxy_ip']
    subnet = ipaddress.ip_network(manifest['subnet'])
    app_ip = str(ipaddress.ip_address(proxy_ip) + 1)
    require(ipaddress.ip_address(app_ip) in subnet and app_ip != str(subnet.broadcast_address),
            'No available test application address')
    for item in json.loads(run('docker', 'network', 'inspect',
                              *run('docker', 'network', 'ls', '-q').split())):
        for entry in item.get('IPAM', {}).get('Config') or []:
            if entry.get('Subnet'):
                require(not subnet.overlaps(ipaddress.ip_network(entry['Subnet'])),
                        'Test subnet now conflicts with Docker network')
    for route in json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'all')):
        if route.get('dst', 'default') != 'default':
            require(not subnet.overlaps(ipaddress.ip_network(route['dst'])), 'Test subnet conflicts with route')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0]) >= 512 * 1024, 'Insufficient available memory for isolated check')
    ids = run('docker', 'ps', '-q', '--filter',
              'label=com.docker.compose.project=shared-booklist-predeploy').split()
    baseline = json.loads(run('docker', 'inspect', *ids))
    require(not any(item['HostConfig'].get('PortBindings') for item in baseline),
            'Existing deployment unexpectedly publishes ports')
    app = next(item for item in baseline if item['Config']['Labels'].get('com.docker.compose.service') == 'app')
    image = app['Image']  # Exact deployed image ID, independent of mutable tags.
    network = 'booklist-https-check-' + uuid.uuid4().hex
    containers = []
    checks = []
    with tempfile.TemporaryDirectory(prefix='booklist-proxy-integration-') as temporary:
        root = Path(temporary)
        root.chmod(0o750)
        os.chown(root, 0, 10001)
        data, tls = root / 'data', root / 'tls'
        for directory in (data, tls):
            directory.mkdir(mode=0o750)
            os.chown(directory, 10001, 10001)
        secret = secrets.token_urlsafe(64)
        environment = root / 'test.env'
        environment.write_text('\n'.join((
            'BOOKLIST_ENV=production', 'BOOKLIST_SECRET_KEY=' + secret,
            'BOOKLIST_ALLOWED_HOSTS=' + domain + ',localhost,127.0.0.1',
            'BOOKLIST_DATA_DIR=/var/lib/shared-booklist',
            'BOOKLIST_TRUSTED_PROXY_IPS=' + proxy_ip)) + '\n')
        environment.chmod(0o600)
        hardening = ['--read-only', '--user', '10001:10001', '--cap-drop', 'ALL',
                     '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:size=32m,mode=1777',
                     '--log-driver', 'none', '--cpus', '0.5']
        app_args = [*hardening, '--memory', '256m', '--env-file', str(environment),
                    '-v', f'{data}:/var/lib/shared-booklist']
        run('docker', 'run', '--rm', '--network', 'none', *app_args,
            image, 'python', 'manage.py', 'migrate', '--noinput')
        if production_certificate:
            source = Path('/etc/shared-booklist/acme-dns-production/live/sharedbooklist-xyz-production')
            require(domain == 'sharedbooklist.xyz', 'Certificate domain does not match prepared configuration')
            for name in ('fullchain.pem', 'privkey.pem'):
                shutil.copyfile(source / name, tls / name)
            run('openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-noout', '-checkhost', domain)
            run('openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-noout', '-checkend', '1209600')
            require(run('openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-noout', '-pubkey') ==
                    run('openssl', 'pkey', '-in', str(tls / 'privkey.pem'), '-pubout'),
                    'Certificate and private key do not match')
            certificate_info = run('openssl', 'x509', '-in', str(tls / 'fullchain.pem'),
                                   '-noout', '-dates', '-issuer', '-serial', '-fingerprint', '-sha256')
        else:
            run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=' + domain, '-addext', 'subjectAltName=DNS:' + domain,
                '-keyout', str(tls / 'privkey.pem'), '-out', str(tls / 'fullchain.pem'))
        for target in tls.iterdir():
            os.chown(target, 0, 10001)
            target.chmod(0o640)
        run('docker', 'network', 'create', '--internal', '--subnet', str(subnet), network)
        try:
            test_app = run('docker', 'run', '--rm', '-d', '--network', network,
                           '--ip', app_ip, '--network-alias', 'app', *app_args, image)
            containers.append(test_app)
            app_pid = run('docker', 'inspect', '-f', '{{.State.Pid}}', test_app)
            for attempt in range(30):
                try:
                    run('docker', 'exec', test_app, 'python', 'scripts/predeploy_health.py')
                    break
                except subprocess.CalledProcessError:
                    if attempt == 29:
                        raise RuntimeError('Disposable application did not become healthy') from None
                    time.sleep(1)
            proxy = run('docker', 'run', '--rm', '-d', '--network', network, '--ip', proxy_ip,
                *hardening, '--memory', '128m',
                '-v', f'{stage}/nginx.https.conf:/etc/nginx/nginx.conf:ro',
                '-v', f'{tls}:/etc/nginx/tls:ro', '-v', f'{stage}/static:/srv/static:ro',
                '--entrypoint', 'nginx', compose['services']['proxy']['image'], '-g', 'daemon off;')
            containers.append(proxy)
            proxy_pid = run('docker', 'inspect', '-f', '{{.State.Pid}}', proxy)
            time.sleep(1)
            jar, headers, body = root / 'cookies', root / 'headers', root / 'body'

            def request(path, *extra, http=False):
                url = ('http://' + domain + ':8080' if http else 'https://' + domain) + path
                connection = (['--resolve', domain + ':8080:127.0.0.1'] if http else
                              ['--connect-to', domain + ':443:127.0.0.1:8443'])
                if not http and not production_certificate:
                    connection += ['--cacert', str(tls / 'fullchain.pem')]
                status = run('nsenter', '-t', proxy_pid, '-n', 'curl', '--noproxy', '*',
                    '--silent', '--show-error', '--max-time', '8', *connection,
                    '-b', str(jar), '-c', str(jar), '-D', str(headers), '-o', str(body),
                    '-w', '%{http_code}', *extra, url)
                return int(status), headers.read_text(), body.read_text()

            status, head, payload = request('/health/')
            require(status == 200 and json.loads(payload)['status'] == 'ok', 'HTTPS application health failed')
            require('strict-transport-security:' in head.lower(), 'HTTPS HSTS missing')
            checks.append('https_health_without_redirect_loop')
            if production_certificate:
                checks.append('production_certificate_hostname_key_and_system_ca_trust')
            for extension, mime in (('css', 'text/css'), ('mjs', 'text/javascript'), ('svg', 'image/svg+xml')):
                asset = next((stage / 'static').rglob('*.' + extension))
                status, head, _ = request('/static/' + asset.relative_to(stage / 'static').as_posix())
                require(status == 200 and 'content-type: ' + mime in head.lower(), 'Static MIME check failed')
            checks.append('css_and_module_mime')
            checks.append('svg_icon_mime')
            require(request('/')[0] == 200, 'Public shelf did not render')
            require(request('/recommend/')[0] == 200, 'Recommendation form did not render')
            checks.append('shelf_and_recommendation_form')
            status, head, _ = request('/recommend/?sort=newest', http=True)
            require(status == 308 and 'location: https://' + domain + '/recommend/?sort=newest' in head.lower(),
                    'HTTP redirect did not preserve path and query on the fixed domain')
            checks.append('http_to_fixed_https_redirect')
            status, head, payload = request('/api/v1/csrf/', '-H', 'X-Forwarded-Proto: http',
                '-H', 'X-Forwarded-For: invalid,attacker', '-H', 'Forwarded: host=attacker.example',
                '-H', 'X-Forwarded-Host: attacker.example', '-H', 'X-Real-IP: 203.0.113.9')
            require(status == 200, 'Proxy failed to overwrite spoofed forwarding headers')
            token = json.loads(payload)['csrf_token']
            require(any('csrftoken=' in line.lower() and 'secure' in line.lower()
                        for line in head.splitlines()), 'CSRF cookie must be Secure')
            checks.append('forwarded_headers_overwritten_and_secure_csrf_cookie')
            write_headers = ['-H', 'Content-Type: application/json', '-H', 'X-CSRFToken: ' + token,
                             '--data', '{}']
            started = int(time.time())
            status, head, payload = request('/api/v1/identity/', *write_headers,
                                           '-H', 'Origin: https://' + domain)
            require(status == 200 and json.loads(payload)['status'] == 'ready', 'Same-origin CSRF POST rejected')
            cookies = SimpleCookie()
            for line in head.splitlines():
                if line.lower().startswith('set-cookie:'):
                    cookies.load(line.partition(':')[2].strip())
            identity = cookies.get('booklist_anonymous')
            require(identity and identity['secure'] and identity['httponly']
                    and identity['samesite'] == 'Lax', 'Identity cookie flags incorrect')
            require(request('/api/v1/identity/', *write_headers,
                            '-H', 'Origin: https://attacker.example')[0] == 403, 'Foreign origin accepted')
            require(request('/api/v1/identity/', '-H', 'Content-Type: application/json',
                            '-H', 'Origin: https://' + domain, '--data', '{}')[0] == 403,
                    'Missing CSRF token accepted')
            require(request('/api/v1/identity/', *write_headers, '-H', 'Origin: https://' + domain,
                            '-H', 'X-Forwarded-For: 203.0.113.99')[0] == 200, 'Spoofed header not overwritten')
            checks.append('same_origin_post_secure_identity_cookie_and_csrf_rejection')
            with sqlite3.connect(data / 'booklist.sqlite3') as database:
                buckets = database.execute('SELECT key,count FROM booklist_ratelimitbucket').fetchall()
                expected = {hmac.new(secret.encode(), f'identity:127.0.0.1:{slot}'.encode(),
                                    hashlib.sha256).hexdigest()
                            for slot in range(started // 3600, int(time.time()) // 3600 + 1)}
                require(buckets and all(key in expected for key, count in buckets)
                        and sum(count for key, count in buckets) == 2, 'Rate limiter used spoofed client identity')
                require(database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Test database integrity failed')
                require(not database.execute('PRAGMA foreign_key_check').fetchall(), 'Test foreign keys failed')
            checks.append('rate_limit_uses_proxy_observed_client_address')
            direct = run('nsenter', '-t', app_pid, '-n', 'curl', '--noproxy', '*',
                '--silent', '--show-error', '--max-time', '8', '--interface', app_ip,
                '-H', 'Host: ' + domain, '-H', 'X-Forwarded-Proto: https', '-o', '/dev/null',
                '-w', '%{http_code}', 'http://' + app_ip + ':8000/health/')
            require(direct == '301', 'Untrusted direct peer bypassed HTTPS redirect')
            checks.append('untrusted_peer_cannot_spoof_tls')
            for container in containers:
                info = json.loads(run('docker', 'inspect', container))[0]
                require(not info['HostConfig'].get('PortBindings'), 'Test container published a port')
        finally:
            for container in reversed(containers):
                subprocess.run(['docker', 'rm', '-f', container], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run('docker', 'network', 'rm', network)
    after = json.loads(run('docker', 'inspect', *ids))
    require([(i['Id'], i['State']['StartedAt']) for i in baseline] ==
            [(i['Id'], i['State']['StartedAt']) for i in after], 'Original containers changed')
    run('docker', 'exec', app['Id'], 'python', 'scripts/predeploy_health.py')
    result = {'stage': str(stage), 'release': release, 'domain': domain, 'checks': checks,
              'production_containers_unchanged': True, 'production_health_ok': True,
              'production_database_mounted': False, 'temporary_resources_removed': True,
              'public_access_enabled': False, 'trusted_certificate_issued': production_certificate,
              'certificate_info': certificate_info, 'automatic_renewal_configured': False}
    receipt_name = 'trusted-integration-receipt.json' if production_certificate else 'integration-receipt.json'
    (stage / receipt_name).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    print('HTTPS_INTEGRATION_SUCCESS', flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        # Do not print arguments containing CSRF tokens or cookie headers.
        print('Isolated verification command failed with exit code ' + str(error.returncode), file=sys.stderr)
        raise SystemExit(1) from None
