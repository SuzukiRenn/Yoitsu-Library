import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('sqlite_backup',Path(__file__).with_name('sqlite_backup.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class BackupTests(unittest.TestCase):
    def test_wal_backup_and_isolated_restore_preserve_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'live.sqlite3'
            with closing(sqlite3.connect(source)) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)')
                connection.execute('INSERT INTO items(value) VALUES (?)',('原始数据',));connection.commit()
                result=module.backup(source,Path(folder)/'backups',True)
                self.assertTrue(result['restore_drill'])
                with closing(sqlite3.connect(Path(folder)/'backups'/result['filename'])) as saved:
                    self.assertEqual(saved.execute('SELECT value FROM items').fetchone(),('原始数据',))
                self.assertEqual(connection.execute('SELECT count(*) FROM items').fetchone(),(1,))

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):module.backup(source,Path(folder)/'backups')
            self.assertFalse(source.exists())

    def test_invalid_database_is_rejected_without_publishing_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'bad.sqlite3';source.write_bytes(b'not sqlite')
            with self.assertRaises(sqlite3.DatabaseError):module.backup(source,Path(folder)/'backups',True)
            self.assertFalse(list((Path(folder)/'backups').glob('*.sqlite3')))


if __name__ == '__main__':unittest.main()
