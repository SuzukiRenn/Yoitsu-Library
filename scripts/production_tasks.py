"""Small independent workers: no scheduler is started inside the WSGI processes."""
from datetime import datetime, timezone
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
        from scripts.retain_sqlite_backups import retain
        while True:
            try:
                result=backup(Path(os.environ['BOOKLIST_DATA_DIR'])/'booklist.sqlite3','/backups',verify_restore=True)
                retention=retain('/backups',Path('/backups')/result['filename'])
                health={'status':'verified','checked_at':datetime.now(timezone.utc).isoformat(),
                        'backup':result,'retention':retention}
                delay=21600
            except Exception as exc:
                health={'status':'needs_attention','checked_at':datetime.now(timezone.utc).isoformat(),
                        'error_type':type(exc).__name__}
                delay=300
            # A private host-side monitor reads this file; no secrets or database rows are exported.
            pending=Path('/backups/.backup-health.tmp')
            pending.write_text(json.dumps(health),encoding='utf-8')
            os.replace(pending,Path('/backups/backup-health.json'))
            print(json.dumps(health),flush=True)
            time.sleep(delay)
    else:
        raise ValueError('unknown_worker')


if __name__ == '__main__':
    main()
