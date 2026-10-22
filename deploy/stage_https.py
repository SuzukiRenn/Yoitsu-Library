"""Stage and verify private HTTPS configuration; never change the running deployment."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def run(*args, capture=True):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def main():
    if os.geteuid() != 0:
        raise SystemExit('Root is required for private configuration staging')
    payload = base64.b64decode(sys.argv[1], validate=True)
    if hashlib.sha256(payload).hexdigest() != sys.argv[2]:
        raise SystemExit('Configuration transfer checksum mismatch')
    files = json.loads(payload)
    manifest = json.loads(files['manifest.json'])
    release = manifest['release']
    current = Path('/var/lib/shared-booklist/predeploy/release.txt').read_text().strip()
    if current != release:
        raise SystemExit('Running release differs; regenerate the preparation bundle')
    compose = json.loads(files['compose.https.json'])
    candidate = ipaddress.ip_network(manifest['subnet'])
    networks = json.loads(run('docker', 'network', 'inspect',
                              *run('docker', 'network', 'ls', '-q').split()))
    for network in networks:
        for item in network.get('IPAM', {}).get('Config') or []:
            if item.get('Subnet') and candidate.overlaps(ipaddress.ip_network(item['Subnet'])):
                raise SystemExit('Candidate subnet overlaps Docker network: ' + network['Name'])
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'all'))
    for route in routes:
        destination = route.get('dst', 'default')
        if destination != 'default' and candidate.overlaps(ipaddress.ip_network(destination)):
            raise SystemExit('Candidate subnet overlaps a server route: ' + destination)
    containers = [json.loads(line) for line in run('docker', 'ps', '--filter',
        'label=com.docker.compose.project=shared-booklist-predeploy', '--format', '{{json .}}').splitlines()]
    container_ids = [item['ID'] for item in containers]
    if not container_ids:
        raise SystemExit('No existing private deployment found')
    inspected = json.loads(run('docker', 'inspect', *container_ids))
    if any(item['HostConfig'].get('PortBindings') for item in inspected):
        raise SystemExit('Existing deployment publishes ports; review its exposure first')
    stage = Path('/etc/shared-booklist/https-staging') / uuid.uuid4().hex
    stage.mkdir(parents=True, mode=0o750)
    os.chown(stage, 0, 10001)
    for name, content in files.items():
        target = stage / name
        target.write_text(content, encoding='utf-8')
        target.chmod(0o640)
        os.chown(target, 0, 10001)
    print('STAGE_PATH=' + str(stage), flush=True)
    app = next(item for item in inspected if item['Config']['Labels'].get(
        'com.docker.compose.service') == 'app')
    static = stage / 'static'
    static.mkdir(mode=0o755)
    run('docker', 'cp', app['Id'] + ':/app/staticfiles/.', str(static))
    for target in static.rglob('*'):
        if target.is_symlink():
            raise SystemExit('Unexpected symlink in exported static assets')
        target.chmod(0o755 if target.is_dir() else 0o644)
    image = compose['services']['proxy']['image']
    run('docker', 'pull', '--platform', 'linux/amd64', image, capture=False)
    common = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
              '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt',
              'no-new-privileges:true', '--tmpfs', '/tmp:size=32m,mode=1777',
              '--memory', '128m', '--cpus', '0.25', '--add-host', 'app:127.0.0.1']
    run(*common, '-v', f'{stage}/nginx.bootstrap.conf:/etc/nginx/nginx.conf:ro',
        '--entrypoint', 'nginx', image, '-t', capture=False)
    with tempfile.TemporaryDirectory(prefix='booklist-tls-check-') as temporary:
        tls = Path(temporary)
        os.chown(tls, 0, 10001)
        tls.chmod(0o750)
        # An isolated, one-day syntax-test certificate. Never installed as the business certificate.
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=' + manifest['domain'], '-addext',
            'subjectAltName=DNS:' + manifest['domain'], '-keyout', str(tls / 'privkey.pem'),
            '-out', str(tls / 'fullchain.pem'))
        for target in tls.iterdir():
            target.chmod(0o640)
            os.chown(target, 0, 10001)
        mounts = ['-v', f'{stage}/nginx.https.conf:/etc/nginx/nginx.conf:ro',
                  '-v', f'{tls}:/etc/nginx/tls:ro', '-v', f'{static}:/srv/static:ro']
        run(*common, *mounts, '--entrypoint', 'nginx', image, '-t', capture=False)
        test_container = run(*common, '-d', *mounts, '--entrypoint', 'nginx',
                             image, '-g', 'daemon off;')
        try:
            namespace_pid = run('docker', 'inspect', '-f', '{{.State.Pid}}', test_container)
            time.sleep(1)
            for extension, expected in (('css', 'text/css'), ('mjs', 'text/javascript'), ('svg', 'image/svg+xml')):
                asset = next(static.rglob('*.' + extension))
                path = asset.relative_to(static).as_posix()
                response = run('nsenter', '-t', namespace_pid, '-n', 'curl',
                    '--noproxy', '*', '--fail', '--silent', '--show-error', '--max-time', '10',
                    '--cacert', str(tls / 'fullchain.pem'), '--resolve',
                    manifest['domain'] + ':8443:127.0.0.1', '-o', '/dev/null', '-w',
                    '%{http_code} %{content_type}',
                    'https://' + manifest['domain'] + ':8443/static/' + path)
                if response != '200 ' + expected:
                    raise SystemExit('TLS/static MIME check failed: ' + response)
        finally:
            run('docker', 'rm', '-f', test_container)
    environment = os.environ.copy()
    environment['BOOKLIST_RELEASE'] = release
    subprocess.run(['docker', 'compose', '-p', 'shared-booklist-predeploy',
        '-f', f'/opt/shared-booklist/releases/{release}/deploy/compose.yaml',
        '-f', str(stage / 'compose.https.json'), 'config', '--quiet'],
        env=environment, stdin=subprocess.DEVNULL, check=True)
    after = json.loads(run('docker', 'inspect', *container_ids))
    if [(i['Id'], i['State']['StartedAt']) for i in inspected] != [
            (i['Id'], i['State']['StartedAt']) for i in after]:
        raise SystemExit('Running containers changed during staging; inspect before proceeding')
    receipt = {'stage': str(stage), 'domain': manifest['domain'], 'release': release,
        'checks': ['subnet_routes', 'existing_no_published_ports', 'bootstrap_nginx_syntax',
                   'tls_nginx_syntax_as_uid_10001', 'isolated_tls_hostname_and_static_mime',
                   'static_export', 'compose_merge', 'containers_unchanged'],
        'public_access_enabled': False, 'active_configuration_changed': False,
        'trusted_certificate_issued': False, 'cloud_security_group_changed': False,
        'remaining': ['proxy integration acceptance with application',
                      'DNS and ICP readiness', 'trusted certificate and renewal drill',
                      'private activation', 'explicit public switch']}
    (stage / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt), flush=True)
    print('HTTPS_STAGING_SUCCESS', flush=True)


if __name__ == '__main__':
    main()
