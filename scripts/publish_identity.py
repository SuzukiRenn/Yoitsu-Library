"""Prepare or deploy an identity release. SSH owns all interactive authentication."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.identity_catalog import export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--source', type=Path, default=ROOT / '.runtime/identity-preview')
    parser.add_argument('--payload', type=Path)
    parser.add_argument('--server-host')
    args = parser.parse_args()
    if not args.prepare_only and (not args.server_host or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args.server_host)):
        parser.error('Use --prepare-only or supply an explicit --server-host')
    if args.payload:
        folder = args.payload.resolve(strict=True)
        manifest = json.loads((folder / 'publish.json').read_text(encoding='utf-8'))
    else:
        branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()
        if branch != 'codex/identity-booklist':
            raise RuntimeError('Only publish from the existing identity branch')
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
            raise RuntimeError('Commit all reviewed release files first')
        release = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        folder = ROOT / '.runtime' / ('identity-publish-' + uuid.uuid4().hex)
        catalog = folder / 'catalog'
        receipt = export(args.source, catalog)
        bundle = folder / 'release.bundle'
        subprocess.run(['git', 'bundle', 'create', str(bundle), 'HEAD'], cwd=ROOT, check=True)
        packet = folder / 'packet.tar'
        with tarfile.open(packet, 'w') as archive:
            archive.add(bundle, arcname='release.bundle', recursive=False)
            for name in ('catalog.json', 'covers.tar', 'manifest.json'):
                archive.add(catalog / name, arcname=name, recursive=False)
        with packet.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest = {'release': release, 'packet_sha256': digest, 'catalog': receipt}
        (folder / 'publish.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (folder / 'bootstrap.py').write_bytes((ROOT / 'deploy/identity_bootstrap.py').read_bytes())
    print(json.dumps({'payload': str(folder), **manifest}), flush=True)
    if args.prepare_only:
        return
    with (folder / 'packet.tar').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest['packet_sha256']:
            raise RuntimeError('Local release packet changed')
    bootstrap = (folder / 'bootstrap.py').read_text(encoding='utf-8-sig')
    remote = 'python3 -c ' + shlex.quote(bootstrap) + ' ' + shlex.quote(manifest['release']) + ' ' + shlex.quote(manifest['packet_sha256'])
    command = ['ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'ServerAliveInterval=30',
               '-o', 'ServerAliveCountMax=3', '-T', 'root@' + args.server_host, remote]
    # Authentication prompts belong to the user's SSH console; no password is read here.
    with (folder / 'packet.tar').open('rb') as packet, (folder / 'deploy.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdin=packet, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        server_receipt = None
        for line in process.stdout:
            print(line, end='', flush=True); log.write(line); log.flush()
            if line.startswith('IDENTITY_DEPLOY_SUCCESS '):
                server_receipt = json.loads(line.split(' ', 1)[1])
        code = process.wait()
    if code:
        raise SystemExit('Deployment stopped. Inspect ' + str(folder / 'deploy.log') + '; do not restore old data blindly.')
    if not server_receipt or server_receipt.get('release') != manifest['release'] or not server_receipt.get('success'):
        raise SystemExit('SSH finished without a matching successful deployment receipt.')
    (folder / 'server-receipt.json').write_text(json.dumps(server_receipt, indent=2), encoding='utf-8')
    print('IDENTITY_PUBLISH_VERIFIED ' + manifest['release'])


if __name__ == '__main__':
    main()
