"""Exercise the actual 0004 -> 0005 -> 0004 -> 0005 path in a new database."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['BOOKLIST_ENV'] = 'test'
os.environ['BOOKLIST_DATA_DIR'] = str(ROOT/'.runtime/public_migration'/uuid.uuid4().hex)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def main():
    import django
    django.setup()
    from datetime import timedelta
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.utils import timezone
    old, new = ('booklist', '0004_offline_import_queue'), ('booklist', '0005_public_recommendations_and_likes')
    executor = MigrationExecutor(connection); executor.migrate([old])
    apps = executor.loader.project_state([old]).apps
    now = timezone.now()
    people = [apps.get_model('booklist', 'AnonymousClient').objects.create(token_hash=str(index)*64, last_seen_at=now, expires_at=now+timedelta(days=1)) for index in (1, 2, 3)]
    work = apps.get_model('booklist', 'Work').objects.create(original_submitted_title='Migration fixture', public_added_at=now)
    vote, reason = apps.get_model('booklist', 'Vote'), apps.get_model('booklist', 'Recommendation')
    vote.objects.create(work=work, anonymous_client=people[0], scope='public')
    reason.objects.create(work=work, anonymous_client=people[0], scope='public', reason='same actor')
    reason.objects.create(work=work, anonymous_client=people[1], scope='public', reason='hidden text still recommended', text_hidden_at=now)
    vote.objects.create(work=work, anonymous_client=people[2], scope='public', cancelled_at=now)
    for target in (new, old, new):
        executor = MigrationExecutor(connection); executor.migrate([target])
        current = executor.loader.project_state([target]).apps
        assert current.get_model('booklist', 'Vote').objects.count() == 2
        assert current.get_model('booklist', 'Recommendation').objects.count() == 2
        if target == new:
            assert current.get_model('booklist', 'PublicRecommendation').objects.count() == 2
            assert current.get_model('booklist', 'PublicLike').objects.count() == 0
            assert set(current.get_model('booklist', 'PublicRecommendation').objects.values_list('anonymous_client_id', flat=True)) == {people[0].pk, people[1].pk}
        with connection.cursor() as cursor:
            assert cursor.execute('PRAGMA foreign_key_check').fetchall() == []
            assert cursor.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    print('Legacy union, cancelled exclusion, independent likes, reverse/forward and integrity passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
