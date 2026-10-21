"""Synthetic 0010 -> 0011 -> 0010 -> 0011 check in a new worktree-local database."""
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
data = ROOT / '.runtime' / ('identity-migration-' + uuid.uuid4().hex)
data.mkdir(parents=True)
os.environ.update({'BOOKLIST_ENV': 'test', 'BOOKLIST_DATA_DIR': str(data), 'DJANGO_SETTINGS_MODULE': 'config.settings'})
sys.path.insert(0, str(ROOT))
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

old, new = ('booklist', '0010_reason_likes'), ('booklist', '0011_accounts_and_notifications')
executor = MigrationExecutor(connection)
executor.migrate([old])
apps = executor.loader.project_state([old]).apps
client = apps.get_model('booklist', 'AnonymousClient').objects.create(token_hash='a'*64,
    last_seen_at=timezone.now(), expires_at=timezone.now()+timezone.timedelta(days=30))
work = apps.get_model('booklist', 'Work').objects.create(original_submitted_title='迁移合成历史书目', public_added_at=timezone.now())
apps.get_model('booklist', 'PublicRecommendation').objects.create(work=work, anonymous_client=client, origin='legacy')
apps.get_model('booklist', 'PublicLike').objects.create(work=work, anonymous_client=client)
reason = apps.get_model('booklist', 'Recommendation').objects.create(work=work, anonymous_client=client, scope='public', reason='迁移合成历史理由')
apps.get_model('booklist', 'ReasonLike').objects.create(recommendation=reason, anonymous_client=client)
apps.get_model('booklist', 'UserWorkMetadata').objects.create(work=work, anonymous_client=client, description='迁移合成历史简介')
tables = ['booklist_anonymousclient', 'booklist_work', 'booklist_publicrecommendation', 'booklist_publiclike',
    'booklist_recommendation', 'booklist_reasonlike', 'booklist_userworkmetadata', 'booklist_changeevent']


def snapshot():
    with connection.cursor() as cursor:
        return {table: cursor.execute('SELECT * FROM '+table+' ORDER BY id').fetchall() for table in tables}


baseline = snapshot()
checks = []
for target, label in [(new, 'upgrade_preserves_all_legacy_rows'), (old, 'empty_upgrade_rollback_preserves_legacy_rows'), (new, 'second_upgrade_preserves_legacy_rows')]:
    MigrationExecutor(connection).migrate([target])
    assert snapshot() == baseline, label
    checks.append(label)
from booklist.models import Account, Notification
assert Account.objects.count() == Notification.objects.count() == 0
checks.append('no_guessed_account_binding_or_historical_notifications')
with connection.cursor() as cursor:
    assert cursor.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert cursor.execute('PRAGMA foreign_key_check').fetchall() == []
checks += ['integrity_ok', 'foreign_keys_ok']
result = {'data_dir': str(data), 'checks': checks, 'synthetic_only': True}
(ROOT/'.runtime'/'identity-migration-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
