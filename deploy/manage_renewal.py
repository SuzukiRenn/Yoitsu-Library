"""Root-only certificate renewal manager; DNS automation without public web ports."""
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path('/etc/shared-booklist/renewal')
ACME = Path('/etc/shared-booklist/acme-dns-production')
NAME = 'sharedbooklist-xyz-production'
DOMAIN = 'sharedbooklist.xyz'
IMAGE = 'certbot/certbot:v5.8.0@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20'


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def stamp(status, **extra):
    value = dict(time=datetime.now(timezone.utc).isoformat(), status=status, **extra)
    temporary = ROOT/'status.pending'
    temporary.write_text(json.dumps(value, indent=2)); temporary.chmod(0o600)
    os.replace(temporary, ROOT/'status.json')
    print(json.dumps(value), flush=True)


def certificate_directory():
    return ACME/'live'/NAME


def certificate_hash():
    return hashlib.sha256((certificate_directory()/'fullchain.pem').read_bytes()).hexdigest()


def certbot(mode):
    command = ['docker', 'run', '--rm', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true', '--memory', '256m', '--cpus', '0.5',
               '--log-driver', 'none', '--tmpfs', '/tmp:size=32m,mode=1777',
               '--tmpfs', '/var/log/letsencrypt:size=16m,mode=1777',
               '--tmpfs', '/var/lib/letsencrypt:size=16m,mode=1777',
               '-v', str(ACME)+':/etc/letsencrypt',
               '-v', str(ROOT/'credentials.json')+':/run/booklist-dns.json:ro',
               '-v', str(ROOT/'dns_hook.py')+':/opt/hooks/dns_hook.py:ro',
               '-v', str(ROOT/'dns-state')+':/var/lib/booklist-dns', IMAGE,
               mode, '--non-interactive', '--cert-name', NAME, '--no-random-sleep-on-renew']
    if mode == 'reconfigure':
        command += ['--manual', '--preferred-challenges', 'dns',
                    '--manual-auth-hook', 'python3 /opt/hooks/dns_hook.py auth',
                    '--manual-cleanup-hook', 'python3 /opt/hooks/dns_hook.py cleanup']
    # Retry one transient CA read timeout, never a DNS authorization/cleanup failure.
    for attempt in range(2):
        name = 'booklist-certificate-'+uuid.uuid4().hex[:12]
        invocation = command[:3]+['--name', name]+command[3:]
        print('CA staging/renewal request started; DNS propagation can take 2 minutes.', flush=True)
        try:
            result = subprocess.run(invocation, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=900)
            print(result.stdout, end='', flush=True)
        finally:
            # A killed Docker client must not leave its container working in the background.
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode == 0:
            return
        if (attempt == 0 and 'ReadTimeout' in result.stdout
                and not list((ROOT/'dns-state').glob('*.json'))):
            print('Temporary CA read timeout; retrying once in 30 seconds.', flush=True)
            time.sleep(30)
            continue
        raise RuntimeError('certificate_request_failed')


def check_pair(directory):
    run('openssl', 'x509', '-in', str(directory/'fullchain.pem'), '-noout', '-checkhost', DOMAIN)
    run('openssl', 'x509', '-in', str(directory/'fullchain.pem'), '-noout', '-checkend', '1209600')
    public = run('openssl', 'x509', '-in', str(directory/'fullchain.pem'), '-pubkey', '-noout')
    private_public = run('openssl', 'pkey', '-in', str(directory/'privkey.pem'), '-pubout')
    if public != private_public:
        raise RuntimeError('certificate_key_mismatch')


def proxy_target(info):
    """Use the proxy's existing binding, including the future public 443 mapping."""
    bindings = info['HostConfig'].get('PortBindings', {})
    for container_port in ['8443/tcp', '443/tcp']:
        entries = bindings.get(container_port) or []
        for entry in entries:
            if entry['HostIp'] in {'127.0.0.1', '0.0.0.0'}:
                return '127.0.0.1:'+entry['HostPort']
        for entry in entries:
            if entry['HostIp'] in {'::', '::1'}:
                return '[::1]:'+entry['HostPort']
    raise RuntimeError('entry_configuration_changed_requires_review')


def publish():
    check_pair(certificate_directory())
    identifiers = run('docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=shared-booklist-predeploy',
                      '--filter', 'label=com.docker.compose.service=proxy',
                      '--filter', 'label=com.docker.compose.oneoff=False').split()
    if len(identifiers) != 1: raise RuntimeError('expected_one_proxy')
    info = json.loads(run('docker', 'inspect', identifiers[0]))[0]
    mounts = [Path(item['Source']) for item in info['Mounts'] if item['Destination'] == '/etc/nginx/tls']
    if len(mounts) != 1: raise RuntimeError('unexpected_tls_mount')
    target = mounts[0]
    if not target.resolve().is_relative_to('/etc/shared-booklist') or target.is_symlink():
        raise RuntimeError('unsafe_tls_path')
    old = {name: (target/name).read_bytes() for name in ['fullchain.pem', 'privkey.pem']}

    def install(contents):
        for name, content in contents.items():
            descriptor, temporary = tempfile.mkstemp(prefix='.renew-', dir=target)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(content); stream.flush(); os.fsync(stream.fileno())
                os.chown(temporary, 0, 10001); os.chmod(temporary, 0o640)
                os.replace(temporary, target/name)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
    try:
        install({name: (certificate_directory()/name).read_bytes() for name in old})
        run('docker', 'exec', identifiers[0], 'nginx', '-t')
        run('docker', 'exec', identifiers[0], 'nginx', '-s', 'reload')
        # Verify the actual served leaf after reload, rather than only checking files.
        import time
        time.sleep(1)
        address = proxy_target(info)
        pem = subprocess.run(['openssl', 's_client', '-connect', address, '-servername', DOMAIN,
                              '-verify_hostname', DOMAIN, '-verify_return_error'], input='',
                             text=True, capture_output=True, timeout=15, check=True).stdout
        served = subprocess.run(['openssl', 'x509', '-noout', '-fingerprint', '-sha256'],
                                input=pem, text=True, capture_output=True, check=True).stdout.strip()
        current = run('openssl', 'x509', '-in', str(certificate_directory()/'fullchain.pem'),
                      '-noout', '-fingerprint', '-sha256')
        if served != current: raise RuntimeError('served_certificate_mismatch')
    except BaseException:
        install(old)
        run('docker', 'exec', identifiers[0], 'nginx', '-t')
        run('docker', 'exec', identifiers[0], 'nginx', '-s', 'reload')
        raise


def install_timer():
    service = '''[Unit]
Description=Shared booklist DNS certificate renewal
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/flock -w 60 /opt/shared-booklist/deploy.lock /usr/bin/python3 /etc/shared-booklist/renewal/manage_renewal.py renew
TimeoutStartSec=20min
UMask=0077
'''
    timer = '''[Unit]
Description=Renew shared booklist certificate twice daily
[Timer]
OnCalendar=*-*-* 03,15:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
'''
    for name, content in [('booklist-certificate.service', service), ('booklist-certificate.timer', timer)]:
        path = Path('/etc/systemd/system')/name
        if path.exists() and path.read_text() != content:
            raise RuntimeError('existing_systemd_unit_differs')
        path.write_text(content); path.chmod(0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'booklist-certificate.timer')
    if run('systemctl', 'is-active', 'booklist-certificate.timer') != 'active':
        raise RuntimeError('timer_not_active')


def main(mode):
    if os.geteuid() != 0: raise RuntimeError('root_required')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ROOT, 0o700)
    if mode == 'setup':
        credentials = ROOT/'credentials.json'
        if not credentials.exists():
            # Newline prompts remain visible even through Windows Tee-Object.
            print('RAM AccessKey ID (input hidden):', flush=True)
            key = getpass.getpass('').strip()
            print('RAM AccessKey Secret (input hidden):', flush=True)
            secret = getpass.getpass('').strip()
            if not key or not secret: raise RuntimeError('empty_credentials')
            with credentials.open('x') as stream:
                os.chmod(credentials, 0o600)
                json.dump({'access_key_id': key, 'access_key_secret': secret}, stream)
        state = ROOT/'dns-state'; state.mkdir(mode=0o700, exist_ok=True)
        # Existing manual renewal config is kept for operator rollback.
        renewal = ACME/'renewal'/(NAME+'.conf')
        backup = ROOT/'manual-renewal.before.conf'
        if not backup.exists(): shutil.copyfile(renewal, backup); backup.chmod(0o600)
        previous = certificate_hash()
        certbot('reconfigure')  # Saves hook configuration only after successful staging issuance.
        if certificate_hash() != previous:
            raise RuntimeError('staging_drill_changed_production_certificate')
        if list(state.glob('*.json')): raise RuntimeError('dns_challenge_cleanup_incomplete')
        publish()  # Exercise validated installation/reload with the current real certificate.
        install_timer()
        stamp('renewal_ready', staging_renewal_verified=True, production_certificate_unchanged=True,
              dns_cleanup_verified=True, nginx_reload_verified=True, timer_active=True)
    elif mode == 'renew':
        certbot('renew')
        # Also handles a previous successful issuance followed by a failed publication.
        publish()
        expiry = run('openssl', 'x509', '-in', str(certificate_directory()/'fullchain.pem'), '-noout', '-enddate')
        stamp('renewal_ok', expiry=expiry, served_certificate_verified=True)
    else:
        raise ValueError('unknown_mode')


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except BaseException:
        stamp('needs_attention', reason='renewal_or_publication_failed; inspect systemd journal')
        raise SystemExit(1) from None
