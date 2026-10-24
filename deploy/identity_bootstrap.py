"""Receive a bounded release packet over authenticated SSH; no credentials are in it."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import uuid


def main():
    release, expected_hash = sys.argv[1:3]
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{40}', release) or not re.fullmatch('[a-f0-9]{64}', expected_hash):
        raise RuntimeError('Invalid release packet request')
    root = Path('/opt/shared-booklist-identity')
    for path in (root, *root.parents):
        if path.is_symlink():
            raise RuntimeError('Linked release root')
    if (root / 'incoming').is_symlink():
        raise RuntimeError('Linked incoming directory')
    incoming = root / 'incoming' / uuid.uuid4().hex
    incoming.mkdir(parents=True, mode=0o700)
    archive = incoming / 'packet.tar'
    digest = hashlib.sha256(); size = 0
    with archive.open('xb') as output:
        for chunk in iter(lambda: sys.stdin.buffer.read(1024 * 1024), b''):
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise RuntimeError('Release packet too large')
            digest.update(chunk); output.write(chunk)
    archive.chmod(0o600)
    if digest.hexdigest() != expected_hash:
        raise RuntimeError('Release packet transfer checksum mismatch')
    allowed = {'release.bundle', 'catalog.json', 'covers.tar', 'manifest.json', 'catalog-increment.json'}
    seen = set()
    with tarfile.open(archive) as stream:
        for member in stream:
            if not member.isfile() or member.name not in allowed or member.name in seen:
                raise RuntimeError('Unexpected release packet member')
            seen.add(member.name)
            with stream.extractfile(member) as source, (incoming / member.name).open('xb') as output:
                output.write(source.read())
            target = incoming / member.name
            if member.name in {'catalog.json', 'covers.tar', 'catalog-increment.json'}:
                # The importer is UID/GID 10001 with all capabilities dropped.
                os.chown(target, 0, 10001); target.chmod(0o640)
            else:
                target.chmod(0o600)
    if not {'release.bundle', 'manifest.json'} <= seen:
        raise RuntimeError('Incomplete release packet')
    manifest = json.loads((incoming / 'manifest.json').read_text())
    update_only = manifest.get('mode') == 'update'
    expected_hashes = set() if update_only else {'catalog.json', 'covers.tar'}
    if 'catalog-increment.json' in seen:
        if not update_only:
            raise RuntimeError('Catalog increment requires an existing deployment')
        expected_hashes.add('catalog-increment.json')
    if set(manifest.get('sha256', {})) != expected_hashes or seen != expected_hashes | {'release.bundle', 'manifest.json'}:
        raise RuntimeError('Incomplete catalog manifest')
    for name, expected_digest in manifest['sha256'].items():
        if name not in {'catalog.json', 'covers.tar', 'catalog-increment.json'}:
            raise RuntimeError('Unexpected manifest member')
        with (incoming / name).open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected_digest:
                raise RuntimeError('Catalog checksum mismatch')
    checkout = root / 'releases' / release
    for path in (checkout, checkout.parent):
        if path.is_symlink():
            raise RuntimeError('Linked release checkout')
    if not checkout.exists():
        checkout.mkdir(parents=True, mode=0o700)
        subprocess.run(['git', '-C', str(checkout), 'init', '-q'], check=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()
    git('bundle', 'verify', str(incoming / 'release.bundle'))
    git('fetch', str(incoming / 'release.bundle'), 'HEAD')
    if git('rev-parse', 'FETCH_HEAD') != release:
        raise RuntimeError('Transferred commit mismatch')
    if git('status', '--porcelain'):
        raise RuntimeError('Existing checkout has changes')
    git('checkout', '--detach', release)
    command = ['python3', 'deploy/identity.py', release]
    if update_only:
        command.append('--update-only')
    else:
        command += ['--catalog', str(incoming / 'catalog.json'), '--covers', str(incoming / 'covers.tar')]
    if 'catalog-increment.json' in seen:
        command += ['--increment', str(incoming / 'catalog-increment.json'),
                    '--increment-sha256', manifest['sha256']['catalog-increment.json']]
    subprocess.run(command, cwd=checkout, check=True, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
