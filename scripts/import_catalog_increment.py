"""Append approved metadata to an existing catalog without changing old records.

The caller provides a SHA256-verified metadata manifest, never a database.
Catalog identities use provider IDs; local primary keys and principals are omitted.
"""
import hashlib
import json
from pathlib import Path
import unicodedata


def import_increment(manifest, sha256, output_dir, *, apply=False):
    payload = Path(manifest).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == sha256, 'Manifest checksum mismatch'
    doc = json.loads(payload)
    rows = doc['works']
    assert doc['format'] == 1 and len(rows) == doc['count'] and 0 < len(rows) <= 6000
    assert len({r['source_metadata']['bili_id'] for r in rows}) == len(rows)
    allowed = {'original_submitted_title', 'title_override', 'title_overridden',
               'author_override', 'author_overridden', 'description_override',
               'description_overridden', 'cover_url_override', 'cover_url_overridden'}
    excluded = {'kakuyomu','syosetu','other','novelpia','游戏剧本','seednovel','webnovel','pixiv','日本轻小说','病矫'}
    for r in rows:
        assert set(r['fields']) == allowed
        assert r['source_metadata']['source_provider'] == 'bilinovel'
        assert r['source_metadata']['edition'] in ('web','book','other')
        assert not set(r['tags']) & excluded
        assert set(r['tags']) == set(r['source_metadata']['approved_tags'])
    from django.conf import settings
    from django.db import connection, transaction
    from booklist.models import Work, WorkAlias, Tag, TagSource, WorkTag
    from scripts.sqlite_backup import backup

    def norm(s):
        return ' '.join(unicodedata.normalize('NFKC', s).split())

    def existing(row):
        found = list(Work.objects.filter(source_metadata__bili_id=row['source_metadata']['bili_id']))
        assert len(found) <= 1, 'Duplicate source ID already exists'
        if found:
            w = found[0]
            assert all(getattr(w,k) == v for k,v in row['fields'].items()), 'Existing source fields differ'
            assert all(w.source_metadata.get(k) == v for k,v in row['source_metadata'].items()), 'Existing source metadata differs'
            assert set(w.workalias_set.filter(archived_at=None).values_list('alias',flat=True)) == {v['alias'] for v in row['aliases']}
            assert set(WorkTag.objects.filter(work=w,state='active',source_type='source').values_list('tag__name',flat=True)) == set(row['tags'])
        return found[0] if found else None

    planned = sum(existing(r) is None for r in rows)
    print(json.dumps({'phase':'validated','would_create':planned,'already_imported':len(rows)-planned}), flush=True)
    if not apply:
        return {'success': True, 'would_create': planned, 'existing_works': len(rows)-planned}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    saved = backup(settings.DATABASES['default']['NAME'], output, verify_restore=True)
    created = []
    new_tags = []
    with transaction.atomic():
        tables = connection.introspection.table_names()
        snapshots = {}
        with connection.cursor() as c:
            for t in tables:
                c.execute(f'SELECT max(rowid) FROM "{t}"')
                limit = c.fetchone()[0] or 0
                c.execute(f'SELECT * FROM "{t}" WHERE rowid<=%s ORDER BY rowid',[limit])
                snapshots[t] = (limit, hashlib.sha256(repr(c.fetchall()).encode()).hexdigest())
        for r in rows:
            if existing(r):
                continue
            w = Work.objects.create(**r['fields'],source_metadata=r['source_metadata'])
            for alias in r['aliases']:
                WorkAlias.objects.create(work=w,**alias)
            for name in r['tags']:
                found = [t for t in Tag.objects.all() if norm(t.name) == norm(name)]
                if not found:
                    tag = Tag.objects.create(name=name, normalized_name=norm(name))
                    new_tags.append(tag.pk)
                else:
                    assert len(found) == 1 and found[0].is_active and found[0].merged_into_id is None, 'Ambiguous/inactive tag'
                    tag = found[0]
                src,_ = TagSource.objects.get_or_create(source_key='bilinovel:'+norm(name), defaults={'tag':tag,'original_name':name})
                assert src.tag_id == tag.pk
                WorkTag.objects.create(work=w,tag=tag,source_type='source')
            created.append({'bili_id':r['source_metadata']['bili_id'],'work_id':w.pk})
        with connection.cursor() as c:
            for t,(limit,digest) in snapshots.items():
                c.execute(f'SELECT * FROM "{t}" WHERE rowid<=%s ORDER BY rowid',[limit])
                assert hashlib.sha256(repr(c.fetchall()).encode()).hexdigest() == digest, 'Existing rows changed: '+t
            assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        assert all(existing(r) for r in rows)
    receipt = {'success':True,'manifest_sha256':sha256,'created_works':len(created),
               'existing_works':len(rows)-len(created),'created_tags':len(new_tags),
               'web_works':sum(r['source_metadata']['edition']=='web' for r in rows),
               'preexisting_rows_unchanged':True,'backup':saved,'created':created}
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt


if __name__ == '__main__':
    import argparse, os, sys
    sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    receipt=import_increment(args.manifest,args.sha256,args.output,apply=args.apply)
    print('CATALOG_INCREMENT_SUCCESS '+json.dumps({k:v for k,v in receipt.items() if k!='created'}),flush=True)
