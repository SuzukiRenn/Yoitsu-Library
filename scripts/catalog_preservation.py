"""Check every preexisting SQLite row across additive migrations/imports."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3


def capture(database):
    result={}
    with closing(sqlite3.connect(Path(database).absolute().as_uri()+'?mode=ro',uri=True)) as db:
        tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            cols=[row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
            limit=db.execute(f'SELECT max(rowid) FROM "{table}"').fetchone()[0] or 0
            names=','.join('"'+c+'"' for c in cols)
            rows=db.execute(f'SELECT {names} FROM "{table}" WHERE rowid<=? ORDER BY rowid',(limit,)).fetchall()
            result[table]={'columns':cols,'max_rowid':limit,'sha256':hashlib.sha256(repr(rows).encode()).hexdigest(),'count':len(rows)}
    return result


def verify(database,before):
    with closing(sqlite3.connect(Path(database).absolute().as_uri()+'?mode=ro',uri=True)) as db:
        for table,entry in before.items():
            names=','.join('"'+c+'"' for c in entry['columns'])
            rows=db.execute(f'SELECT {names} FROM "{table}" WHERE rowid<=? ORDER BY rowid',(entry['max_rowid'],)).fetchall()
            if hashlib.sha256(repr(rows).encode()).hexdigest()!=entry['sha256']:
                raise RuntimeError('Preexisting data changed: '+table)
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Foreign key check failed')
    return True
