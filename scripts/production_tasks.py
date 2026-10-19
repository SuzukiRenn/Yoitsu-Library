"""Small independent workers: no scheduler is started inside the WSGI processes."""
import json
import os
from pathlib import Path
import sys
import time

# Executed as a file from /app/scripts; make the repository importable.
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))


def main():
    mode = sys.argv[1]
    if mode == 'scheduler':
        os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
        import django
        django.setup()
        from django.core.management import call_command
        cleanup_at = 0
        while True:
            call_command('advance_activities')
            if time.monotonic() >= cleanup_at:
                call_command('cleanup_security')
                cleanup_at = time.monotonic()+86400
            print('scheduler_ok',flush=True)
            time.sleep(30)
    elif mode == 'backup':
        from scripts.sqlite_backup import backup
        while True:
            result=backup(Path(os.environ['BOOKLIST_DATA_DIR'])/'booklist.sqlite3','/backups',verify_restore=True)
            print(json.dumps(result),flush=True)
            time.sleep(21600)
    else:
        raise ValueError('unknown_worker')


if __name__ == '__main__':
    main()
