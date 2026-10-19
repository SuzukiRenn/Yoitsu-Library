"""Fresh-database offline import evidence. Does not make website or API requests."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).absolute().parents[1]
FIXTURE=ROOT/'tests/fixtures/wenku8'


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    out=ROOT/'.runtime/import_verification'/datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True)
    data=out/'data'
    data.mkdir()
    (ROOT/'.runtime/tmp').mkdir(parents=True,exist_ok=True)
    env={**os.environ,'BOOKLIST_ENV':'test','BOOKLIST_DATA_DIR':str(data),'PYTHONIOENCODING':'utf-8',
         'TEMP':str(ROOT/'.runtime/tmp'),'TMP':str(ROOT/'.runtime/tmp')}
    protected=[]
    for folder in ('wenku8_html','wenku8_pages','cases'):
        protected.extend(p for p in (ROOT/'tests'/folder).rglob('*') if p.is_file())
    browser=ROOT/'tests/wenku8_browser'
    protected.extend(list(browser.glob('*.py'))+list(browser.glob('*.md'))+list(browser.glob('*.txt')))
    for folder in ('online_search_20261018','selected_detail_20261018','logging_online_review_20261018','chrome_regression_20261018'):
        protected.extend(p for p in (browser/folder).rglob('*') if p.is_file())
    baseline={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    (out/'legacy_baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2),encoding='utf-8')
    results={}

    def command(name,args):
        run=subprocess.run([sys.executable,'-B',*args],cwd=ROOT,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=120)
        (out/(name+'.log')).write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
        results[name]={'exit_code':run.returncode}
        print(name,run.returncode,flush=True)
        if run.returncode:
            raise RuntimeError(name+' failed')
        return run.stdout

    def state():
        with sqlite3.connect(str(data/'booklist.sqlite3')) as db:
            return {'works':db.execute('SELECT count(*) FROM booklist_work').fetchone()[0],
                    'events':db.execute('SELECT count(*) FROM booklist_changeevent').fetchone()[0]}

    try:
        command('pip_check',['-m','pip','check'])
        command('check',['manage.py','check'])
        command('migrate',['manage.py','migrate','--noinput'])
        command('migration_drift',['manage.py','makemigrations','--check','--dry-run'])
        command('all_tests',['manage.py','test','booklist.tests','--noinput','-v','2'])
        first=json.loads(command('pause',['manage.py','import_wenku8','--manifest',str(FIXTURE/'manifest.json'),'--max-items','1']))
        assert first['status']=='paused' and first['counts']['pending']==2
        resumed=json.loads(command('resume',['manage.py','import_wenku8','--resume',first['batch_id']]))
        assert resumed['status']=='completed' and resumed['processed_this_run']==2
        skipped=json.loads(command('skip_completed',['manage.py','import_wenku8','--resume',first['batch_id']]))
        assert skipped['processed_this_run']==0
        before=state()
        duplicate=json.loads(command('repeat_new_batch',['manage.py','import_wenku8','--manifest',str(FIXTURE/'manifest.json')]))
        assert duplicate['status']=='completed'
        after=state()
        checks={'three_works':after['works']==3,'no_duplicate_or_spurious_events':before==after,
                'pause_resume':True,'completed_items_skipped':True}
        expected={book:FIXTURE/'expected'/f'{book}.json' for book in (4151,3219,2951)}
        with sqlite3.connect(str(data/'booklist.sqlite3')) as db:
            for book,path in expected.items():
                old=json.loads(path.read_text(encoding='utf-8'))
                row=db.execute('SELECT id,wenku8_title,wenku8_author,wenku8_description,wenku8_cover_url,source_metadata,original_submitted_title,public_added_at FROM booklist_work WHERE wenku8_book_id=?',(book,)).fetchone()
                actual_tags={r[0] for r in db.execute("SELECT t.name FROM booklist_worktag wt JOIN booklist_tag t ON wt.tag_id=t.id WHERE wt.work_id=? AND wt.source_type='source' AND wt.state='active'",(row[0],))}
                checks[str(book)+'_historical_fields_match']=(row[1:5]==(old['title'],old['author'],old['introduction'],old['cover_url']) and json.loads(row[5])['word_count']==old['word_count'] and actual_tags==set(old['tags']))
                checks[str(book)+'_not_public_no_original_input']=row[6] is None and row[7] is None
            checks['foreign_keys_valid']=db.execute('PRAGMA foreign_key_check').fetchall()==[]
            checks['integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            checks['seventeen_source_links']=db.execute("SELECT count(*) FROM booklist_worktag WHERE source_type='source' AND state='active'").fetchone()[0]==17
            checks['twelve_source_tags']=db.execute('SELECT count(*) FROM booklist_tag').fetchone()[0]==12
        results['import_checks']=checks
        assert all(checks.values())
        results['success']=True
    except Exception as exc:
        results.update(success=False,error_class=type(exc).__name__,failed_step=str(exc) if type(exc) in (RuntimeError,AssertionError) else 'Inspect logs')
    changed=[p for p,h in baseline.items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    results['protected_legacy_files']=len(baseline)
    results['changed_legacy_files']=changed
    results['success']=results['success'] and not changed
    results['scope']='offline only; no source site, paid API, production host or GitHub request'
    (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('EVIDENCE:',out,flush=True)
    return 0 if results['success'] else 1


if __name__=='__main__':
    raise SystemExit(main())
