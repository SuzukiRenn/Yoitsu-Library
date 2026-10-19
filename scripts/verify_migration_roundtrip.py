"""Exercise SQLite trigger preservation on a copy of an already verified local backup."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence_directory', type=Path)
    args = parser.parse_args()
    out = args.evidence_directory.absolute() / 'migration_roundtrip'
    out.mkdir(exist_ok=False)
    shutil.copy2(args.evidence_directory/'restored.sqlite3', out/'booklist.sqlite3')
    env = {**os.environ, 'BOOKLIST_ENV':'test', 'BOOKLIST_DATA_DIR':str(out), 'PYTHONIOENCODING':'utf-8'}
    results = {}
    for name, arguments in [('reverse', ['migrate','booklist','0002','--noinput']), ('forward',['migrate','--noinput'])]:
        run = subprocess.run([sys.executable,'-B',str(ROOT/'manage.py'),*arguments], cwd=ROOT, env=env,
            capture_output=True,encoding='utf-8',errors='replace',timeout=60)
        (out/(name+'.log')).write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
        results[name]=run.returncode
        if run.returncode:
            break
    if all(v==0 for v in results.values()) and len(results)==2:
        db=sqlite3.connect(str(out/'booklist.sqlite3'))
        triggers={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        results['triggers_preserved']={'track_activity_insert','track_activity_update','track_sourcefieldstate_insert','last_admin_update','immutable_changeevent_delete'}.issubset(triggers)
        results['foreign_keys_valid']=db.execute('PRAGMA foreign_key_check').fetchall()==[]
        results['schema_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        results['original_admin_retained']=db.execute('SELECT count(*) FROM booklist_admin').fetchone()[0]==1
        db.close()
    results['success']=results.get('reverse')==0 and results.get('forward')==0 and all(results.get(k) is True for k in ('triggers_preserved','foreign_keys_valid','schema_integrity','original_admin_retained'))
    (out/'summary.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results))
    return 0 if results['success'] else 1


if __name__=='__main__':
    raise SystemExit(main())
