"""Deploy a committed identity release into its own private server instance."""
import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.identity_config import ROOT, CONFIG, STATE, PROJECT, DOMAIN, SUBNET, PROXY, PORT, TLS, configuration


def run(*args, capture=False, timeout=600):
    result = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None, timeout=timeout)
    return result.stdout.strip() if capture else None


def private(path, uid=0):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError('Refusing linked deployment path')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid not in (0, uid):
        raise RuntimeError('Unexpected deployment directory owner')
    os.chown(path, uid, uid); path.chmod(0o700)


def verify_network():
    wanted = ipaddress.ip_network(SUBNET)
    ids = run('docker', 'network', 'ls', '-q', capture=True).split()
    owned = False
    if ids:
        for net in json.loads(run('docker', 'network', 'inspect', *ids, capture=True)):
            for item in net.get('IPAM', {}).get('Config', []) or []:
                if not item.get('Subnet'):
                    continue
                actual = ipaddress.ip_network(item['Subnet'])
                if actual.version == 4 and wanted.overlaps(actual):
                    if net['Name'] != PROJECT + '_edge' or actual != wanted or not net['Internal']:
                        raise RuntimeError('Identity subnet collides with an existing network')
                    owned = True
    for route in json.loads(run('ip', '-j', '-4', 'route', capture=True)):
        dst = route.get('dst', 'default')
        if dst != 'default':
            actual = ipaddress.ip_network(dst, strict=False)
            if wanted.overlaps(actual) and not (owned and actual == wanted):
                raise RuntimeError('Identity subnet collides with a host route')


def anonymous_snapshot():
    ids = run('docker', 'ps', '-aq', '--filter',
              'label=com.docker.compose.project=shared-booklist-predeploy', capture=True).split()
    if not ids:
        raise RuntimeError('Expected anonymous containers were not found; inspect server state first')
    rows = json.loads(run('docker', 'inspect', *ids, capture=True))
    return {row['Name']: {'id': row['Id'], 'image': row['Image'],
                          'started_at': row['State']['StartedAt'],
                          'ports': row['HostConfig']['PortBindings']} for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release'); parser.add_argument('--catalog'); parser.add_argument('--covers')
    parser.add_argument('--update-only', action='store_true')
    parser.add_argument('--increment'); parser.add_argument('--increment-sha256')
    args = parser.parse_args()
    release = args.release
    if args.increment and (not args.update_only or not re.fullmatch('[a-f0-9]{64}', args.increment_sha256 or '')):
        raise RuntimeError('Increment requires an explicit existing-deployment update and checksum')
    if args.update_only and (not (STATE / 'release.txt').is_file() or not (STATE / 'data/booklist.sqlite3').is_file()):
        raise RuntimeError('Update-only deployment requires existing release and database; never initialize')
    if not args.update_only and not (args.catalog and args.covers):
        raise RuntimeError('Initial deployment requires catalog and covers')
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{40}', release):
        raise RuntimeError('Root operator and explicit full release required')
    checkout = ROOT / 'releases' / release
    if Path.cwd().resolve() != checkout or run('git', 'rev-parse', 'HEAD', capture=True) != release:
        raise RuntimeError('Release checkout mismatch')
    if run('git', 'status', '--porcelain', capture=True):
        raise RuntimeError('Release checkout is modified')
    private(ROOT)
    # Serialize with existing anonymous deploys without modifying that release or its services.
    with open('/opt/shared-booklist/deploy.lock', 'a') as shared_lock, open(ROOT / 'deploy.lock', 'a') as own_lock:
        deadline = time.monotonic() + 30
        for stream in (shared_lock, own_lock):
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Another deployment holds the lock; retry later')
                    time.sleep(1)
        if shutil.disk_usage('/').free < 3 * 1024**3:
            raise RuntimeError('Need 3 GiB free')
        if int(run('docker', 'version', '--format', '{{.Server.Version}}', capture=True).split('.')[0]) < 28:
            raise RuntimeError('Docker 28+ required for loopback publication')
        anonymous_before = anonymous_snapshot()
        verify_network()
        if not (STATE / 'release.txt').exists():
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', PORT))
        run('openssl', 'x509', '-in', TLS + '/fullchain.pem', '-noout', '-checkhost', DOMAIN)
        run('openssl', 'x509', '-in', TLS + '/fullchain.pem', '-noout', '-checkend', '1209600')
        print('PHASE build_identity_and_run_linux_regression', flush=True)
        run('docker', 'build', '--label', 'org.opencontainers.image.revision=' + release, '--target', 'runtime', '-t', 'shared-booklist-identity:' + release,
            '-f', 'deploy/Dockerfile', '.', timeout=7200)
        run('docker', 'build', '--target', 'verification', '-t', 'shared-booklist-identity-verify:' + release,
            '-f', 'deploy/Dockerfile', '.', timeout=3600)
        verify = ['docker', 'run', '--rm', '--network', 'none', '--memory', '512m', '--cpus', '1',
                  '--read-only', '--tmpfs', '/tmp:size=128m', '--tmpfs', '/app/.runtime:size=32m,uid=10001,gid=10001,mode=0700',
                  'shared-booklist-identity-verify:' + release]
        run(*verify, timeout=1800)
        run(*verify, 'sh', '-c', 'set -e; for f in scripts/test_*.mjs; do node "$f"; done; python scripts/test_deploy.py', timeout=600)
        for check in ('scripts/test_launch_operations.py', 'scripts/test_operational_safety.py', 'scripts/test_offsite_retention.py'):
            run(*verify, 'python', check, timeout=600)
        for path, uid in ((CONFIG, 0), (STATE, 0), (STATE / 'data', 10001), (STATE / 'backups', 10001)):
            private(path, uid)
        env = CONFIG / 'production.env'
        if env.is_symlink():
            raise RuntimeError('Linked configuration')
        if not env.exists():
            with env.open('x') as output:
                output.write('BOOKLIST_ENV=production\nBOOKLIST_SECRET_KEY=' + secrets.token_urlsafe(64)
                             + '\nBOOKLIST_DATA_DIR=/var/lib/shared-booklist-identity\nBOOKLIST_ALLOWED_HOSTS='
                             + DOMAIN + ',localhost,127.0.0.1\nBOOKLIST_TRUSTED_PROXY_IPS=' + PROXY + '\n')
            env.chmod(0o600)
        if env.stat().st_uid != 0 or env.stat().st_mode & 0o077:
            raise RuntimeError('Production config must be root-private')
        compose, nginx = configuration(release)
        # Versioned config means a failed candidate never rewrites a running Compose file.
        definition = CONFIG / ('compose-' + release + '.json')
        definition.write_text(json.dumps(compose, indent=2)); definition.chmod(0o600)
        nginx_path = CONFIG / ('nginx-' + release + '.conf')
        if nginx_path.is_symlink():
            raise RuntimeError('Linked nginx config')
        nginx_path.write_text(nginx); nginx_path.chmod(0o644)
        CONFIG.chmod(0o750); os.chown(CONFIG, 0, 10001)
        static = STATE / 'static' / release
        private(static)
        container = run('docker', 'create', '--network', 'none', 'shared-booklist-identity:' + release, capture=True)
        try:
            run('docker', 'cp', container + ':/app/staticfiles/.', str(static))
        finally:
            run('docker', 'rm', container)
        # Proxy runs as UID 10001; persistent data/config secrets remain private.
        for path in (STATE, STATE / 'static', static):
            os.chown(path, 0, 10001); path.chmod(0o750)
        for path in static.rglob('*'):
            if path.is_symlink():
                raise RuntimeError('Linked static asset')
            path.chmod(0o755 if path.is_dir() else 0o644)
        command = ['docker', 'compose', '-p', PROJECT, '-f', str(definition)]
        run(*command, 'config', '--quiet')
        tool = command + ['run', '-T', '--rm', '--no-deps', 'tools']
        run(*tool, 'python', 'scripts/check_production.py')
        proxy_image = compose['services']['proxy']['image']
        run('docker', 'run', '--rm', '--network', 'none', '--read-only', '--user', '10001:10001',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:size=32m',
            '--add-host', 'app:127.0.0.1', '-v', str(nginx_path) + ':/etc/nginx/nginx.conf:ro',
            '-v', TLS + ':/etc/nginx/tls:ro', '--entrypoint', 'nginx', proxy_image, '-t')
        old_release = (STATE / 'release.txt').read_text().strip() if (STATE / 'release.txt').exists() else None
        stopped = False
        try:
            run(*command, 'stop', 'app', 'scheduler', 'backup', 'proxy'); stopped = True
            database = STATE / 'data' / 'booklist.sqlite3'
            if database.exists():
                run(*command, 'run', '-T', '--rm', '--no-deps', 'backup', 'python', 'scripts/sqlite_backup.py',
                    '--source', '/var/lib/shared-booklist-identity/booklist.sqlite3', '--output-dir', '/backups', '--verify-restore')
            from scripts.catalog_preservation import capture, verify
            preserved = capture(database) if database.exists() else None
            run(*tool, 'python', 'manage.py', 'migrate', '--noinput')
            if not old_release and not args.update_only:
                receipt = run(*command, 'run', '-T', '--rm', '--no-deps', '-v',
                    str(Path(args.catalog).resolve()) + ':/tmp/identity-catalog.json:ro',
                    '-v', str(Path(args.covers).resolve()) + ':/tmp/identity-covers.tar:ro',
                    'tools', 'python', 'scripts/identity_catalog.py',
                    'verify' if (STATE / 'initial-catalog.json').exists() else 'load',
                    '--database', '/var/lib/shared-booklist-identity/booklist.sqlite3',
                    '--catalog', '/tmp/identity-catalog.json', '--covers', '/tmp/identity-covers.tar',
                    capture=True)
                for path in (STATE / 'data').rglob('*'):
                    if path.is_symlink():
                        raise RuntimeError('Linked identity data')
                    os.chown(path, 10001, 10001); path.chmod(0o700 if path.is_dir() else 0o600)
                (STATE / 'initial-catalog.json').write_text(receipt + '\n')
            increment_receipt = None
            if args.increment:
                print('PHASE append_approved_catalog_increment', flush=True)
                payload = run(*command, 'run', '-T', '--rm', '--no-deps', '-v',
                    str(Path(args.increment).resolve()) + ':/tmp/catalog-increment.json:ro',
                    'tools', 'python', 'scripts/import_catalog_increment.py',
                    '--manifest', '/tmp/catalog-increment.json', '--sha256', args.increment_sha256,
                    '--output', '/var/lib/shared-booklist-identity/catalog-increments/' + release,
                    '--apply', capture=True, timeout=600)
                print(payload, flush=True)
                increment_receipt = json.loads(next(line.split(' ', 1)[1] for line in payload.splitlines()
                    if line.startswith('CATALOG_INCREMENT_SUCCESS ')))
                if not increment_receipt.get('success') or not increment_receipt.get('preexisting_rows_unchanged'):
                    raise RuntimeError('Catalog increment did not preserve existing rows')
            if preserved is not None:
                verify(database, preserved)
                print('PREEXISTING_IDENTITY_DATA_VERIFIED', flush=True)
            run(*command, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120')
            run(*command, 'exec', '-T', 'app', 'python', 'scripts/predeploy_health.py')
            for path in ('/health/', '/account/login/', '/account/register/', '/static/account.mjs', '/static/account.css', '/static/appearance.css', '/static/appearance.mjs',
                         '/static/shelf-view-motion.mjs', '/static/reason-reference-editor.mjs', '/static/edition-badge.mjs'):
                run('curl', '--noproxy', '*', '--fail', '--silent', '--show-error', '--resolve',
                    DOMAIN + ':' + str(PORT) + ':127.0.0.1', '-o', '/dev/null',
                    'https://' + DOMAIN + ':' + str(PORT) + path)
            app_id = run(*command, 'ps', '-q', 'app', capture=True)
            proxy_id = run(*command, 'ps', '-q', 'proxy', capture=True)
            app_info, proxy_info = [json.loads(run('docker', 'inspect', cid, capture=True))[0] for cid in (app_id, proxy_id)]
            if app_info['HostConfig']['PortBindings']:
                raise RuntimeError('Application must not publish ports')
            if proxy_info['HostConfig']['PortBindings'] != {'8443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}]}:
                raise RuntimeError('Unexpected proxy port binding')
            run(*command, 'run', '-T', '--rm', '--no-deps', 'backup', 'python', 'scripts/sqlite_backup.py',
                '--source', '/var/lib/shared-booklist-identity/booklist.sqlite3', '--output-dir', '/backups', '--verify-restore')
            if anonymous_snapshot() != anonymous_before:
                raise RuntimeError('Anonymous containers changed during identity deployment; inspect before proceeding')
            (STATE / 'anonymous-containers.json').write_text(json.dumps(anonymous_before, indent=2))
            receipt = {'success': True, 'release': release, 'bind': '127.0.0.1:' + str(PORT),
                       'public_access': False, 'tls_system_ca_verified': True, 'anonymous_services_modified': False,
                       'previous_release': old_release, 'update_only': args.update_only, 'preexisting_data_preserved': preserved is not None,
                       'catalog_increment': increment_receipt}
            (STATE / 'receipt.json').write_text(json.dumps(receipt, indent=2))
            (STATE / 'release.txt').write_text(release + '\n')
            new = ROOT / 'current.new'
            if new.exists() or new.is_symlink():
                raise RuntimeError('Unexpected pending release pointer')
            new.symlink_to(checkout); os.replace(new, ROOT / 'current')
            print('IDENTITY_DEPLOY_SUCCESS ' + json.dumps(receipt), flush=True)
        except BaseException:
            if stopped:
                subprocess.run(command + ['stop', 'app', 'scheduler', 'backup', 'proxy'], check=False)
                print('Identity services stopped; data retained. Do not restore an old database blindly.', flush=True)
            raise


if __name__ == '__main__':
    main()
