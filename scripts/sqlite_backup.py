"""Consistent SQLite backup and isolated restore drill. Never overwrites a live DB."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid


def validate(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
        raise RuntimeError('backup_database_has_no_tables')
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise RuntimeError('backup_integrity_failed')
    if connection.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('backup_foreign_keys_failed')


def logical_digest(connection):
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode('utf-8')); digest.update(b'\n')
    return digest.hexdigest()


def backup(source, output_dir, verify_restore=False):
    source = Path(source).absolute()
    if not source.is_file(): raise FileNotFoundError('source_database_missing')
    output = Path(output_dir).absolute()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = datetime.now(timezone.utc).strftime('booklist-%Y%m%dT%H%M%S-')+uuid.uuid4().hex[:12]+'.sqlite3'
    final = output / filename
    with tempfile.TemporaryDirectory(prefix='.backup-',dir=output) as staging:
        temp = Path(staging)/'backup.sqlite3'
        started=time.monotonic()
        def progress(*_):
            if time.monotonic()-started > 120: raise TimeoutError('backup_budget_exceeded')
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10)) as original, closing(sqlite3.connect(temp)) as saved:
            original.backup(saved, pages=256, sleep=.1, progress=progress)
            validate(saved)
            if verify_restore:
                with closing(sqlite3.connect(Path(staging)/'restore.sqlite3')) as restored:
                    saved.backup(restored,pages=256,progress=progress); validate(restored)
                    if logical_digest(saved) != logical_digest(restored):
                        raise RuntimeError('restore_content_mismatch')
        os.chmod(temp, 0o600)
        with temp.open('r+b') as stream: os.fsync(stream.fileno())
        os.link(temp, final)  # Atomic publication; fails rather than overwriting any existing name.
        if os.name == 'posix':
            directory = os.open(output, os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
    digest=hashlib.sha256()
    with final.open('rb') as stream:
        for chunk in iter(lambda:stream.read(65536),b''): digest.update(chunk)
    return {'status':'verified','filename':filename,'bytes':final.stat().st_size,
        'sha256':digest.hexdigest(),'restore_drill':bool(verify_restore)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--verify-restore', action='store_true')
    args = parser.parse_args()
    print(json.dumps(backup(args.source,args.output_dir,args.verify_restore)))
