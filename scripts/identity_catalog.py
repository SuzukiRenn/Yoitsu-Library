"""Copy only catalog metadata and validated covers into a fresh identity database."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tarfile

TABLES = ('booklist_work', 'booklist_tag', 'booklist_sourcefieldstate',
          'booklist_tagsource', 'booklist_workalias', 'booklist_worktag')
EMPTY = ('booklist_account', 'booklist_accountsession', 'booklist_admin',
         'booklist_adminsession', 'booklist_anonymousclient', 'booklist_activity',
         'booklist_recommendation', 'booklist_vote', 'booklist_publicrecommendation',
         'booklist_publiclike', 'booklist_notification', 'booklist_worklikeevent',
         'booklist_reasonlike', 'booklist_feedback', 'booklist_userworkmetadata')
COVER = re.compile(r'[0-9a-f]{64}\.(?:jpg|png)(?:\.json)?\Z')


def read_only(path):
    path = Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def export(source, output):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    payload = {'format': 1, 'tables': {}}
    with closing(read_only(source / 'booklist.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        suppressed = set(tuple(row) for row in db.execute(
            "SELECT work_id,tag_id FROM booklist_tagsuppression WHERE is_active=1 AND scope IN ('all','source')"))
        for table in TABLES:
            rows = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
            if table == 'booklist_worktag':
                rows = [row for row in rows if row['source_type'] == 'source'
                        and row['anonymous_client_id'] is None and row['admin_id'] is None
                        and (row['work_id'], row['tag_id']) not in suppressed]
            if table == 'booklist_workalias':
                # Administrator identities are not part of a catalog package.
                for row in rows:
                    row['created_by_id'] = None
            payload['tables'][table] = rows
    (output / 'catalog.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    cover_count = 0
    with tarfile.open(output / 'covers.tar', 'w') as archive:
        root = source / 'covers'
        for metadata in sorted(root.glob('*.json')):
            if not COVER.fullmatch(metadata.name) or metadata.is_symlink():
                continue
            image = metadata.with_suffix('')
            if not image.is_file() or image.is_symlink():
                continue
            info = json.loads(metadata.read_text(encoding='utf-8'))
            raw = image.read_bytes()
            if (not 0 < len(raw) <= 1024 * 1024 or info['bytes'] != len(raw)
                    or hashlib.sha256(raw).hexdigest() != info['sha256']):
                raise ValueError('Invalid cached cover')
            archive.add(image, arcname=image.name, recursive=False)
            archive.add(metadata, arcname=metadata.name, recursive=False)
            cover_count += 1
    hashes = {}
    for name in ('catalog.json', 'covers.tar'):
        with (output / name).open('rb') as stream:
            hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    receipt = {'catalog_only': True, 'accounts_and_interactions_exported': False,
               'counts': {table: len(rows) for table, rows in payload['tables'].items()},
               'covers': cover_count,
               'sha256': hashes}
    (output / 'manifest.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def load(database, catalog):
    payload = json.loads(Path(catalog).read_text(encoding='utf-8'))
    if payload.get('format') != 1 or set(payload.get('tables', {})) != set(TABLES):
        raise ValueError('Catalog table whitelist mismatch')
    with closing(sqlite3.connect(database)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            for table in (*TABLES, *EMPTY):
                if db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                    raise ValueError('Import requires an empty identity database: ' + table)
            db.execute('PRAGMA defer_foreign_keys=ON')
            for table in TABLES:
                columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
                rows = payload['tables'][table]
                for row in rows:
                    if set(row) != set(columns):
                        raise ValueError('Catalog columns differ: ' + table)
                    if table == 'booklist_worktag' and (row['source_type'] != 'source'
                            or row['admin_id'] is not None or row['anonymous_client_id'] is not None):
                        raise ValueError('Catalog cannot carry contribution principals')
                    if table == 'booklist_workalias' and row['created_by_id'] is not None:
                        raise ValueError('Catalog cannot carry administrator ownership')
                sql = 'INSERT INTO ' + table + ' (' + ','.join('"' + col + '"' for col in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
                db.executemany(sql, [tuple(row[col] for col in columns) for row in rows])
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Catalog foreign keys failed')
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Catalog integrity failed')
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {'imported': {table: len(payload['tables'][table]) for table in TABLES},
            'accounts_and_interactions_empty': True}


def verify(database, catalog, covers=None):
    payload = json.loads(Path(catalog).read_text(encoding='utf-8'))
    if payload.get('format') != 1 or set(payload.get('tables', {})) != set(TABLES):
        raise ValueError('Catalog table whitelist mismatch')
    with closing(read_only(database)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        for table in TABLES:
            rows = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
            if rows != payload['tables'][table]:
                raise ValueError('Existing catalog differs: ' + table)
        for table in EMPTY:
            if db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                raise ValueError('Existing identity data requires review: ' + table)
        if [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)]:
            raise ValueError('Existing identity integrity failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Existing identity foreign keys failed')
    count = 0
    if covers:
        root = Path(database).parent / 'covers'
        with tarfile.open(covers) as stream:
            for member in stream:
                if not member.isfile() or not COVER.fullmatch(member.name):
                    raise ValueError('Unsafe cover archive member')
                target = root / member.name
                if target.is_symlink() or not target.is_file() or target.stat().st_size != member.size:
                    raise ValueError('Existing cover differs')
                with stream.extractfile(member) as expected, target.open('rb') as actual:
                    if hashlib.file_digest(expected, 'sha256').digest() != hashlib.file_digest(actual, 'sha256').digest():
                        raise ValueError('Existing cover content differs')
                if not member.name.endswith('.json'):
                    count += 1
    return {'catalog_matches': True, 'accounts_and_interactions_empty': True,
            'works': len(payload['tables']['booklist_work']), 'covers_verified': count}


def install_covers(archive, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as stream:
        for member in stream:
            if not member.isfile() or not COVER.fullmatch(member.name) or not 0 < member.size <= 1024 * 1024:
                raise ValueError('Unsafe cover archive member')
            with stream.extractfile(member) as source, (target / member.name).open('xb') as output:
                output.write(source.read())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    out = sub.add_parser('export'); out.add_argument('--source', required=True); out.add_argument('--output', required=True)
    inp = sub.add_parser('load'); inp.add_argument('--database', required=True); inp.add_argument('--catalog', required=True); inp.add_argument('--covers')
    check = sub.add_parser('verify'); check.add_argument('--database', required=True); check.add_argument('--catalog', required=True); check.add_argument('--covers')
    args = parser.parse_args()
    if args.mode == 'export':
        result = export(args.source, args.output)
    elif args.mode == 'load':
        result = load(args.database, args.catalog)
    else:
        result = verify(args.database, args.catalog, args.covers)
    if args.mode == 'load' and args.covers:
        install_covers(args.covers, Path(args.database).parent / 'covers')
    print(json.dumps(result, ensure_ascii=False))
