"""Persistent, process-shared read throttling without locking the catalog database."""
import sqlite3
from contextlib import closing
from django.db import OperationalError


def consume(path, key, limit, expires_at, now):
    if limit < 1:
        return False
    try:
        with closing(sqlite3.connect(path, timeout=2, isolation_level=None)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS read_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL)')
            row = db.execute('INSERT INTO read_limits(key,count,expires_at) VALUES (?,1,?) '
                'ON CONFLICT(key) DO UPDATE SET count=count+1 WHERE count < ? RETURNING count',
                (key, expires_at, limit)).fetchone()
            # Expiry is indexed; cleanup remains a short statement in this separate database.
            db.execute('CREATE INDEX IF NOT EXISTS read_limits_expiry ON read_limits(expires_at)')
            db.execute('DELETE FROM read_limits WHERE expires_at <= ?', (now,))
            return row is not None
    except sqlite3.OperationalError as exc:
        raise OperationalError('Read rate-limit storage unavailable') from exc
