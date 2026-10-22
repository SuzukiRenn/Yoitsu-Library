"""Create a private, consistent disaster-recovery bundle; never restore a live DB."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
import sys

DATA = Path('/var/lib/shared-booklist/predeploy/data')
CONFIG = Path('/etc/shared-booklist')
OUTPUT = Path('/var/lib/shared-booklist/offsite-export')


def validate(db):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise RuntimeError('integrity_failed')
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('foreign_keys_failed')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='booklist_work'").fetchone():
        raise RuntimeError('wrong_database')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create():
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    OUTPUT.mkdir(mode=0o700, exist_ok=True)
    OUTPUT.chmod(0o700)
    if shutil.disk_usage(OUTPUT).free < 2 * 1024**3:
        raise RuntimeError('insufficient_export_space')
    release = Path('/opt/shared-booklist/current').resolve(strict=True)
    revision = subprocess.check_output(['git', '-C', str(release), 'rev-parse', 'HEAD'], text=True).strip()
    name = 'booklist-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:8]+'.tar'
    final = OUTPUT/name
    entries = {}
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=OUTPUT) as temporary:
        staging = Path(temporary)
        db_path = staging/'booklist.sqlite3'
        with closing(sqlite3.connect((DATA/'booklist.sqlite3').as_uri()+'?mode=ro', uri=True)) as live:
            with closing(sqlite3.connect(db_path)) as saved:
                live.backup(saved, pages=256, sleep=.1)
                validate(saved)
                works = saved.execute('SELECT COUNT(*) FROM booklist_work').fetchone()[0]
        archive = staging/'archive.tar'
        with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT) as tar:
            def add(path, archive_name, allowed_root=None):
                resolved = path.resolve(strict=True)
                if allowed_root and not resolved.is_relative_to(allowed_root.resolve()):
                    raise RuntimeError('linked_file_outside_backup_scope')
                if not resolved.is_file():
                    raise RuntimeError('non_regular_backup_file')
                before = digest(resolved)
                info = tar.gettarinfo(str(resolved), arcname=archive_name)
                info.uid = info.gid = 0; info.uname = info.gname = ''; info.mode = 0o600
                with resolved.open('rb') as stream:
                    tar.addfile(info, stream)
                if before != digest(resolved):
                    raise RuntimeError('file_changed_during_backup')
                entries[archive_name] = {'sha256': before, 'bytes': info.size}

            add(db_path, 'data/booklist.sqlite3')
            cover_count = 0
            for path in sorted((DATA/'covers').rglob('*')):
                if path.is_dir(): continue
                add(path, 'data/covers/'+path.relative_to(DATA/'covers').as_posix(), DATA/'covers')
                if path.suffix.lower() in {'.jpg', '.jpeg', '.png'}: cover_count += 1
            # ACME symlinks are flattened into portable regular files within that tree only.
            for relative in ['predeploy.env', 'ssh-access/nginx.conf', 'ssh-access/compose.json',
                             'ssh-access/tls', 'acme-dns-production', 'renewal', 'source-updater']:
                path = CONFIG/relative
                if not path.exists():
                    if relative in {'renewal', 'source-updater'}: continue  # Optional before feature setup.
                    raise RuntimeError('required_configuration_missing')
                paths = sorted(path.rglob('*')) if path.is_dir() else [path]
                for item in paths:
                    if item.is_dir(): continue
                    if item.name.endswith('.lock'): continue
                    add(item, 'config/'+item.relative_to(CONFIG).as_posix(), path if path.is_dir() else CONFIG)
            # Preserve public diagnostic/checkpoint JSON, but never export a live
            # login cookie jar. A restore can authenticate with the private account.
            updates = DATA/'source_updates'
            if updates.is_dir():
                for item in sorted(updates.glob('*.json')):
                    add(item, 'data/source_updates/'+item.name, updates)
            code = staging/'release.tar'
            subprocess.run(['git', '-C', str(release), 'archive', '--format=tar', '-o', str(code), revision], check=True)
            add(code, 'release/source.tar')
            manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'release': revision, 'works': works, 'cover_files': cover_count,
                        'files': entries, 'database_check': 'ok', 'foreign_key_errors': 0}
            raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
            info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o600
            tar.addfile(info, io.BytesIO(raw))
        archive.chmod(0o600)
        os.link(archive, final)
    receipt = {'status': 'snapshot_ready', 'filename': name, 'bytes': final.stat().st_size,
               'sha256': digest(final), 'works': works, 'cover_files': cover_count, 'release': revision}
    with (OUTPUT/(name+'.json')).open('x') as stream:
        json.dump(receipt, stream)
    print(json.dumps(receipt), flush=True)


def dispatch(command):
    # This is the ONLY command surface exposed to the restricted offsite SSH key.
    if command == 'snapshot':
        create()
        return
    match = re.fullmatch(r'(download|ack) (booklist-[0-9TZ]+-[a-f0-9]{8}\.tar)(?: ([a-f0-9]{64}))?', command)
    if not match:
        raise ValueError('unsupported_backup_command')
    operation, filename, expected = match.groups()
    archive = OUTPUT/filename
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('missing_regular_snapshot')
    if operation == 'download' and expected is None:
        with archive.open('rb') as stream:
            shutil.copyfileobj(stream, sys.stdout.buffer)
    elif operation == 'ack' and expected and digest(archive) == expected:
        # Caller acknowledges ONLY after its independent local restore drill passes.
        archive.unlink()
        (OUTPUT/(filename+'.json')).unlink()
        print('acknowledged_verified_offsite_copy')
    else:
        raise ValueError('invalid_acknowledgement')


if __name__ == '__main__':
    if sys.argv[1:] == ['--ssh-dispatch']:
        dispatch(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    elif not sys.argv[1:]:
        create()
    else:
        raise ValueError('unsupported_arguments')
