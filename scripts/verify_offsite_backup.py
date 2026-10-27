"""Verify every received bundle member, then restore its DB into temporary storage."""
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tarfile
import tempfile


def verify(archive, receipt):
    archive = Path(archive)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['sha256']:
            raise ValueError('transport_hash_mismatch')
    if archive.stat().st_size != receipt['bytes']:
        raise ValueError('transport_size_mismatch')
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        names = [entry.name for entry in members]
        if len(names) != len(set(names)):
            raise ValueError('duplicate_members')
        for entry in members:
            name = PurePosixPath(entry.name)
            if not entry.isfile() or name.is_absolute() or '..' in name.parts or '\\' in entry.name:
                raise ValueError('unsafe_archive_member')
        manifest = json.load(tar.extractfile('manifest.json'))
        if receipt.get('edition') != manifest.get('edition'):
            raise ValueError('backup_edition_mismatch')
        if set(names) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('manifest_inventory_mismatch')
        for name, expected in manifest['files'].items():
            with tar.extractfile(name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected['sha256'] or tar.getmember(name).size != expected['bytes']:
                raise ValueError('member_hash_mismatch')
        # Never unpack configuration/secrets or overwrite production data during a drill.
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=archive.parent) as temporary:
            saved = Path(temporary)/'saved.sqlite3'
            with tar.extractfile('data/booklist.sqlite3') as source, saved.open('xb') as output:
                shutil.copyfileobj(source, output)
            restored = Path(temporary)/'restored.sqlite3'
            with closing(sqlite3.connect(saved.as_uri()+'?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(restored)) as target:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('restore_integrity_failed')
                    if target.execute('PRAGMA foreign_key_check').fetchone():
                        raise ValueError('restore_foreign_keys_failed')
                    works = target.execute('SELECT COUNT(*) FROM booklist_work').fetchone()[0]
                    if works != manifest['works'] or works != receipt['works']:
                        raise ValueError('restore_count_mismatch')
                    if list(source.iterdump()) != list(target.iterdump()):
                        raise ValueError('restore_contents_mismatch')
    return dict(receipt, status='offsite_verified', all_file_hashes_verified=True,
                isolated_restore_verified=True, database_check='ok', foreign_key_errors=0)


if __name__ == '__main__':
    archive = Path(sys.argv[1]).resolve()
    result = verify(archive, json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig')))
    report = archive.with_suffix('.verified.json')
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
