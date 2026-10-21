"""Run an isolated loopback preview. Never reads .env or production data."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--seed', action='store_true', help='Seed only an empty preview database')
    args = parser.parse_args()
    if args.port == 8765 or not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged port other than the anonymous site port 8765')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            parser.error('Preview port already in use; choose another --port')
    data = ROOT / '.runtime' / 'identity-preview'
    data.mkdir(parents=True, exist_ok=True)
    temp = ROOT / '.runtime' / 'preview-temp'
    temp.mkdir(parents=True, exist_ok=True)
    secret = data / 'preview-secret.key'
    if not secret.exists():
        secret.write_text(secrets.token_urlsafe(64), encoding='ascii')
    os.environ.update({'BOOKLIST_ENV': 'development', 'BOOKLIST_DATA_DIR': str(data),
        'BOOKLIST_SECRET_KEY': secret.read_text(encoding='ascii'), 'BOOKLIST_ALLOWED_HOSTS': 'localhost,127.0.0.1',
        'BOOKLIST_TRUSTED_PROXY_IPS': '', 'BOOKLIST_ICP_RECORD': '', 'TEMP': str(temp), 'TMP': str(temp),
        'DJANGO_SETTINGS_MODULE': 'config.settings', 'PYTHONDONTWRITEBYTECODE': '1'})
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', interactive=False, verbosity=1)
    if args.seed:
        call_command('seed_identity_demo')
    print(f'Identity preview: http://127.0.0.1:{args.port}/', flush=True)
    call_command('runserver', f'127.0.0.1:{args.port}', use_reloader=False)


if __name__ == '__main__':
    main()
