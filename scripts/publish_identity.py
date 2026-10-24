"""Prepare or deploy an identity release. SSH owns all interactive authentication."""
import argparse
import codecs
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
    parser.add_argument('--update-only', action='store_true', help='Require an existing identity deployment; never initialize catalog or accounts')
    parser.add_argument('--increment', type=Path, help='Approved metadata-only catalog increment')
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
        folder.mkdir(parents=True)
        catalog = folder / 'catalog'
        if args.increment and not args.update_only:
            parser.error('--increment requires --update-only')
        if args.update_only:
            receipt = {'mode': 'update', 'accounts_and_interactions_exported': False}
        else:
            receipt = export(args.source, catalog)
        content = {'mode': 'update' if args.update_only else 'initial', 'sha256': {}}
        if args.increment:
            payload = args.increment.read_bytes()
            doc = json.loads(payload)
            if doc.get('format') != 1 or doc.get('count') != len(doc.get('works', [])):
                raise RuntimeError('Invalid catalog increment')
            (folder / 'catalog-increment.json').write_bytes(payload)
            content['sha256']['catalog-increment.json'] = hashlib.sha256(payload).hexdigest()
            receipt['increment_works'] = doc['count']
        if not args.update_only:
            content['sha256'].update(json.loads((catalog / 'manifest.json').read_text())['sha256'])
        (folder / 'manifest.json').write_text(json.dumps(content), encoding='utf-8')
        bundle = folder / 'release.bundle'
        subprocess.run(['git', 'bundle', 'create', str(bundle), 'HEAD'], cwd=ROOT, check=True)
        packet = folder / 'packet.tar'
        with tarfile.open(packet, 'w') as archive:
            archive.add(bundle, arcname='release.bundle', recursive=False)
            archive.add(folder / 'manifest.json', arcname='manifest.json', recursive=False)
            if not args.update_only:
                for name in ('catalog.json', 'covers.tar'):
                    archive.add(catalog / name, arcname=name, recursive=False)
            if args.increment:
                archive.add(folder / 'catalog-increment.json', arcname='catalog-increment.json', recursive=False)
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
        process = subprocess.Popen(command, stdin=packet, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        server_receipt = None
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        while chunk := process.stdout.read1(4096):
            text = decoder.decode(chunk)
            # SSH password prompts and unittest progress may not end in a newline.
            print(text, end='', flush=True); log.write(text); log.flush()
            pending += text
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
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
