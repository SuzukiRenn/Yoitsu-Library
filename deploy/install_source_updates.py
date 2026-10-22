"""Install only after a successful server preview and initial bounded refresh."""
import json
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sqlite3
import time


def call(*args):
    subprocess.run(args, check=True)


def install(source, destination, mode):
    if destination.is_symlink():
        raise RuntimeError('linked_operator_configuration')
    if destination.is_file() and destination.read_bytes() != source.read_bytes():
        backup = destination.with_name(destination.name+'.before-'+str(time.time_ns()))
        backup.write_bytes(destination.read_bytes()); backup.chmod(0o600)
    temporary = destination.with_name('.'+destination.name+'.pending')
    if temporary.is_symlink():
        raise RuntimeError('linked_operator_configuration')
    temporary.write_bytes(source.read_bytes()); temporary.chmod(mode)
    os.replace(temporary, destination)


def database_evidence():
    path = Path('/var/lib/shared-booklist/predeploy/data/booklist.sqlite3')
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('database_integrity_requires_review')
        protected = {}
        for table in ('publicrecommendation', 'publiclike', 'recommendation', 'vote', 'activitywork', 'userworkmetadata'):
            rows = db.execute('SELECT * FROM booklist_'+table+' ORDER BY id').fetchall()
            protected[table] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
        rows = db.execute('SELECT id,original_submitted_title,title_override,title_overridden,author_override,author_overridden,'
                          'description_override,description_overridden,cover_url_override,cover_url_overridden,'
                          'public_added_at,hidden_at,merged_into_id FROM booklist_work ORDER BY id').fetchall()
        return {'protected': protected, 'overrides': dict((row[0], row[1:]) for row in rows),
                'works': len(rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive-empty-preview-title-stop', action='store_true',
                        help='Operator-reviewed recovery of the initial title parser preview; never an imported run')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'booklist-source-update.service'])
    if active.returncode == 0:
        raise RuntimeError('source_update_already_running')
    root = Path('/etc/shared-booklist/source-updater')
    if root.is_symlink() or not (root/'credentials.json').is_file():
        raise RuntimeError('saved_source_credentials_missing')
    release = Path('/opt/shared-booklist/current').resolve(strict=True)
    install(release/'deploy/run_source_update.py', root/'run_source_update.py', 0o700)
    install(release/'deploy/offsite_snapshot.py', Path('/etc/shared-booklist/offsite_snapshot.py'), 0o600)
    for name in ('booklist-source-update.service', 'booklist-source-update.timer'):
        install(release/'deploy'/name, Path('/etc/systemd/system')/name, 0o644)
    call('systemd-analyze', 'verify', '/etc/systemd/system/booklist-source-update.service',
         '/etc/systemd/system/booklist-source-update.timer')
    call('systemctl', 'daemon-reload')
    if args.archive_empty_preview_title_stop:
        checkpoint = Path('/var/lib/shared-booklist/predeploy/data/source_updates/daily.json')
        if checkpoint.is_symlink():
            raise RuntimeError('linked_source_checkpoint')
        state = json.loads(checkpoint.read_text())
        if state.get('status') != 'needs_attention' or state.get('reason') != 'source_listing_title_changed' or state.get('items') or state.get('last_success_at'):
            raise RuntimeError('preview_checkpoint_not_eligible_for_reviewed_recovery')
        archived = checkpoint.with_name('preview-title-stop-'+str(time.time_ns())+'.json')
        checkpoint.rename(archived)
        print('REVIEWED_EMPTY_PREVIEW_STOP_ARCHIVED '+archived.name, flush=True)
    print('SOURCE_UPDATE_READ_ONLY_PREVIEW', flush=True)
    call('flock', '-w', '30', '/opt/shared-booklist/deploy.lock', 'python3', str(root/'run_source_update.py'), '--dry-run')
    preview = json.loads(Path('/var/lib/shared-booklist/predeploy/data/source_updates/dry_run.json').read_text())
    if preview['status'] != 'dry_run' or len(preview['items']) > 200:
        raise RuntimeError('source_update_preview_requires_review')
    print('SOURCE_UPDATE_INITIAL_BOUNDED_RUN', flush=True)
    before = database_evidence()
    call('systemctl', 'start', 'booklist-source-update.service')
    state = json.loads(Path('/var/lib/shared-booklist/predeploy/data/source_updates/daily.json').read_text())
    if state['status'] not in {'completed', 'completed_with_errors'}:
        raise RuntimeError('source_update_initial_run_requires_review')
    after = database_evidence()
    if before['protected'] != after['protected'] or any(after['overrides'].get(pk) != value for pk, value in before['overrides'].items()):
        raise RuntimeError('protected_data_changed_requires_review')
    call('systemctl', 'enable', '--now', 'booklist-source-update.timer')
    call('systemctl', 'list-timers', '--all', 'booklist-source-update.timer', '--no-pager')
    print(json.dumps({'status': 'daily_update_enabled', 'first_run_status': state['status'],
        'works_before': before['works'], 'works_after': after['works'],
        'database_integrity': 'ok', 'foreign_key_errors': 0, 'protected_data_unchanged': True,
        'imported': sum(item['status']=='imported' for item in state['items']),
        'failed': [item for item in state['items'] if item['status']=='failed'],
        'pending_identity_review': [item['title'] for item in state['items'] if item.get('pending_identity_review')]}), flush=True)


if __name__ == '__main__':
    main()
