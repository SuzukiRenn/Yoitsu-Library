import sqlite3
from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.core.exceptions import ImproperlyConfigured


def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != 'sqlite':
        return
    if sqlite3.sqlite_version_info < (3, 51, 3):
        raise ImproperlyConfigured('SQLite >= 3.51.3 required for this release')
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA foreign_keys=ON')
        if cursor.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise ImproperlyConfigured('SQLite foreign keys must be enabled')
        cursor.execute('PRAGMA journal_mode=WAL')


class BooklistConfig(AppConfig):
    name = 'booklist'

    def ready(self):
        connection_created.connect(configure_sqlite, dispatch_uid='booklist_sqlite')
