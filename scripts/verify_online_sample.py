"""One fixed online sample into a new isolated local DB, never production.

Uses the existing two-GET robots/transport budget. No source body is persisted.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).absolute().parents[1]


def import_and_check(raw):
    from django.db import transaction
    from booklist.importing import apply_snapshot, validate_snapshot, SOURCE_COLUMNS
    from booklist.models import Work, WorkTag, ChangeEvent
    from integrations.wenku8.offline import extract, SnapshotError
    snapshot = extract(raw, 4151, 'https://www.wenku8.net/book/4151.htm')
    fields, warnings = validate_snapshot(snapshot)
    if warnings or not snapshot['document_diagnostics']['complete'] or fields['tags'].get('complete') is not True:
        raise SnapshotError('online_sample_not_complete')
    with transaction.atomic():
        work, warnings, created = apply_snapshot(snapshot)
        if warnings:
            raise SnapshotError('online_import_warnings')
        events = ChangeEvent.objects.count()
        repeated, repeated_warnings, repeated_created = apply_snapshot(snapshot)
        work.refresh_from_db()
        actual_tags = set(WorkTag.objects.filter(work=work, source_type='source', state='active').values_list('tag__name', flat=True))
        expected_tags = {entry['name'] for entry in fields['tags']['value'] or []}
        checks = {
            'created_once': created and not repeated_created and repeated.pk == work.pk,
            'one_source_work': Work.objects.filter(wenku8_book_id=4151).count() == 1,
            'source_columns_match': all(getattr(work, column) == fields[name]['value'] for name, column in SOURCE_COLUMNS.items()),
            'source_metadata_match': all(work.source_metadata.get(name) == fields[name]['value'] for name in ('publisher_category', 'status', 'last_updated', 'word_count')),
            'tags_match': actual_tags == expected_tags,
            'no_repeat_business_events': ChangeEvent.objects.count() == events,
            'no_repeat_warnings': not repeated_warnings,
            'not_public': work.public_added_at is None,
            'no_original_input': work.original_submitted_title is None,
        }
        if not all(checks.values()):
            raise SnapshotError('online_import_readback_failed')
    return {'result': 'identity_verified_and_imported', 'book_id': 4151,
            'title': fields['title']['value'], 'tag_count': len(actual_tags),
            'field_states': {name: field['state'] for name, field in fields.items()},
            'document_diagnostics': snapshot['document_diagnostics'], 'checks': checks,
            'scope': 'isolated local verification database; no public contributions or production deployment'}


def main():
    sys.path.insert(0, str(ROOT))
    out = ROOT/'.runtime/online_import_verification'/(
        datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    # Explicitly ignore incoming data-dir/production settings for this verifier.
    os.environ.update(BOOKLIST_ENV='test', BOOKLIST_DATA_DIR=str(out/'data'), DJANGO_SETTINGS_MODULE='config.settings')
    result = {'success': False, 'scope': 'isolated local DB; transport provenance in probe.scope; no automatic retry'}
    try:
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection
        from integrations.wenku8.offline import parser
        from scripts.probe_wenku8 import probe, check_metadata
        parser()  # fail before networking if the parser runtime is unavailable
        with (out/'migrate.log').open('w', encoding='utf-8') as log:
            call_command('migrate', interactive=False, stdout=log, verbosity=1)
        received = {}
        def diagnose(raw):
            metadata = check_metadata(raw)
            received['raw'] = raw
            return metadata
        result['probe'] = probe('local', metadata_validator=diagnose)
        # Keep network/identity diagnostics distinct from the database phase.
        # A failed transport never supplies raw bytes to this import step.
        if result['probe']['outcome'] == 'detail_identity_verified_optional_fields_unvalidated':
            result['import'] = import_and_check(received.pop('raw'))
        imported = result.get('import', {}).get('result') == 'identity_verified_and_imported'
        with connection.cursor() as cursor:
            foreign_keys = cursor.execute('PRAGMA foreign_key_check').fetchall() == []
            integrity = cursor.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        result.update(success=imported and foreign_keys and integrity,
                      foreign_keys_valid=foreign_keys, integrity=integrity)
    except Exception as exc:
        result['error_class'] = type(exc).__name__
    (out/'summary.json').write_text(json.dumps(result, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print('EVIDENCE:', out)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
