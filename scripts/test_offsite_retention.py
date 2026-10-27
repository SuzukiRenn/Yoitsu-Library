"""Isolated retention regressions: real SQLite restore, expiry, and failure protection."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from retain_offsite_backups import run, guard

NOW = datetime(2026, 10, 27, 2, tzinfo=timezone.utc)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.number = 0

    def bundle(self, age, *, corrupt_db=False):
        self.number += 1
        when = NOW - timedelta(days=age)
        name = 'booklist-' + when.strftime('%Y%m%dT%H%M%SZ-') + f'{self.number:08x}.tar'
        archive = self.root / name
        database = self.root / f'fixture-{self.number}.sqlite3'
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE booklist_work (id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO booklist_work VALUES (1)')
            db.commit()
        data = b'not a database' if corrupt_db else database.read_bytes()
        manifest = {'works': 1, 'files': {'data/booklist.sqlite3':
            {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}}}
        with tarfile.open(archive, 'w') as tar:
            for filename, raw in [('data/booklist.sqlite3', data), ('manifest.json', json.dumps(manifest).encode())]:
                info = tarfile.TarInfo(filename); info.size = len(raw); tar.addfile(info, io.BytesIO(raw))
        receipt = {'status': 'snapshot_ready', 'filename': name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                   'bytes': archive.stat().st_size, 'works': 1, 'release': 'a' * 40}
        (self.root / (name + '.receipt.json')).write_text(json.dumps(receipt), encoding='utf-8')
        archive.with_suffix('.verified.json').write_text(json.dumps(dict(receipt, status='offsite_verified',
            all_file_hashes_verified=True, isolated_restore_verified=True, database_check='ok', foreign_key_errors=0)), encoding='utf-8')
        return archive

    def test_daily_and_four_older_weekly_windows(self):
        ages = [0, 1, 7, 8, 9, 14, 15, 16, 21, 22, 23, 28, 29, 30, 35, 36, 100]
        files = {age: self.bundle(age) for age in ages}
        plan = run(self.root, now=NOW)
        kept = {row['name'] for row in plan['kept']}
        self.assertEqual(kept, {files[a].name for a in [0, 1, 7, 8, 15, 22, 29]})
        self.assertEqual(len(plan['delete']), 10)
        self.assertTrue(all(path.exists() for path in files.values()))

    def test_latest_survives_even_when_all_old(self):
        newest = self.bundle(100); self.bundle(101)
        plan = run(self.root, now=NOW)
        self.assertEqual([r['name'] for r in plan['kept']], [newest.name])

    def test_latest_weekly_copy_does_not_keep_second_copy_in_same_window(self):
        newest = self.bundle(8); self.bundle(9)
        plan = run(self.root, now=NOW)
        self.assertEqual([r['name'] for r in plan['kept']], [newest.name])

    def test_apply_deletes_only_expired_verified_trios(self):
        anchor = self.bundle(0); weekly = self.bundle(8); expired = self.bundle(9); too_old = self.bundle(40)
        unknown = self.root / 'personal-note.txt'; unknown.write_text('keep')
        partial = self.root / 'booklist-test.tar.partial'; partial.write_bytes(b'keep')
        result = run(self.root, now=NOW, apply=True, after_backup=anchor)
        self.assertEqual(set(result['deleted']), {expired.name, too_old.name})
        for file in [expired, too_old]:
            self.assertFalse(file.exists()); self.assertFalse(file.with_suffix('.verified.json').exists())
            self.assertFalse((self.root / (file.name + '.receipt.json')).exists())
        self.assertTrue(anchor.exists() and weekly.exists() and unknown.exists() and partial.exists())
        events = [json.loads(line)['event'] for line in (self.root / '.retention-history.jsonl').read_text().splitlines()]
        self.assertEqual(events, ['plan', 'deleted', 'deleted', 'complete'])

    def test_no_anchor_means_no_deletion(self):
        old = self.bundle(40)
        with self.assertRaisesRegex(ValueError, 'fresh_verified'):
            run(self.root, now=NOW, apply=True)
        self.assertTrue(old.exists())

    def test_stale_or_nonlatest_anchor_refused(self):
        old = self.bundle(40); stale = self.bundle(2)
        with self.assertRaisesRegex(ValueError, 'anchor_must'):
            run(self.root, now=NOW, apply=True, after_backup=stale)
        anchor = self.bundle(0)
        with self.assertRaisesRegex(ValueError, 'anchor_must'):
            run(self.root, now=NOW, apply=True, after_backup=stale)
        self.assertTrue(old.exists() and anchor.exists())

    def test_unverified_tampered_and_bad_receipts_protected(self):
        anchor = self.bundle(0)
        missing = self.bundle(40); missing.with_suffix('.verified.json').unlink()
        tampered = self.bundle(41); tampered.write_bytes(b'tampered')
        bad = self.bundle(42); bad.with_suffix('.verified.json').write_text('{invalid')
        result = run(self.root, now=NOW, apply=True, after_backup=anchor)
        self.assertEqual(len(result['protected']), 3)
        self.assertEqual(result['deleted'], [])
        self.assertTrue(missing.exists() and tampered.exists() and bad.exists())

    def test_restore_failure_does_not_delete_old_backup(self):
        old = self.bundle(40); anchor = self.bundle(0, corrupt_db=True)
        with self.assertRaises(sqlite3.DatabaseError):
            run(self.root, now=NOW, apply=True, after_backup=anchor)
        self.assertTrue(old.exists() and anchor.exists())

    def test_concurrent_retention_is_rejected(self):
        self.bundle(0)
        with guard(self.root):
            with self.assertRaises(OSError):
                run(self.root, now=NOW)

    def test_linked_backup_file_is_protected(self):
        import os
        anchor = self.bundle(0); old = self.bundle(40)
        os.link(old, self.root / 'kept-hardlink')
        result = run(self.root, now=NOW, apply=True, after_backup=anchor)
        self.assertEqual(result['deleted'], [])
        self.assertEqual(result['protected'][0]['name'], old.name)
        self.assertTrue(old.exists())


if __name__ == '__main__':
    unittest.main()
