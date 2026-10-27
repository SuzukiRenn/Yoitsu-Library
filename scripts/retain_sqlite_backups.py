"""Retention for verified server SQLite copies; legacy files without receipts are protected."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from scripts.retain_offsite_backups import safe_root, fingerprint, guard, digest, choose, read_json
from scripts.sqlite_backup import validate

NAME = re.compile(r'booklist-(\d{8}T\d{6})-[a-f0-9]{12}\.sqlite3')


def retain(directory, current, *, apply=True, now=None):
    now = now or datetime.now(timezone.utc)
    root = safe_root(directory)
    with guard(root):
        valid, protected = [], []
        for path in sorted(root.iterdir()):
            match = NAME.fullmatch(path.name)
            if not match:
                continue
            report = path.with_suffix('.verified.json')
            try:
                stamps = [fingerprint(p, root) for p in [path, report]]
                receipt = read_json(report)
                if (receipt.get('status') != 'verified' or receipt.get('restore_drill') is not True
                        or receipt.get('filename') != path.name or receipt.get('bytes') != stamps[0][2]
                        or receipt.get('sha256') != digest(path)):
                    raise ValueError('invalid_verified_receipt')
                valid.append({'name':path.name,'when':datetime.strptime(match[1],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc),
                              'paths':[path,report],'stamps':stamps,'sha256':receipt['sha256'],'bytes':sum(s[2] for s in stamps)})
            except (OSError, ValueError, KeyError, TypeError):
                protected.append(path.name)
        valid.sort(key=lambda b:(b['when'], b['name']),reverse=True)
        kept, expired = choose(valid,now)
        result={'kept':[b['name'] for b in kept],'delete':[b['name'] for b in expired],
                'protected_legacy_or_invalid':protected,'deleted':[],'reclaimable_bytes':sum(b['bytes'] for b in expired)}
        if not apply:
            return result
        anchor=Path(current).absolute()
        if not valid or anchor != valid[0]['paths'][0] or not timedelta(0)<=now-valid[0]['when']<=timedelta(days=1):
            raise ValueError('fresh_verified_sqlite_backup_required')
        with closing(sqlite3.connect(anchor.as_uri()+'?mode=ro',uri=True)) as db:
            validate(db)
        log=root/'.retention-history.jsonl'
        if log.exists() or log.is_symlink(): fingerprint(log,root)
        def record(event):
            with log.open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(time=now.isoformat(),**event))+'\n')
        record({'event':'plan',**result})
        for item in expired:
            for b in [*kept,item]:
                if b['stamps'] != [fingerprint(p,root) for p in b['paths']]:
                    raise ValueError('backup_changed_before_cleanup')
            if digest(item['paths'][0]) != item['sha256']:raise ValueError('backup_hash_changed')
            for path in item['paths']:path.unlink()
            result['deleted'].append(item['name']);record({'event':'deleted','name':item['name']})
        record({'event':'complete','deleted_count':len(result['deleted'])})
        return result
