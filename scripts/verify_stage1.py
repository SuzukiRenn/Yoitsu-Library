"""Local phase-1 evidence: fresh DB, tests, real loopback HTTP, backup, old-code integrity."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).absolute().parents[1]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    out = ROOT / '.runtime/verification' / datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True)
    data = out / 'data'
    data.mkdir()
    paths = []
    for folder in ('wenku8_html','wenku8_pages','cases'):
        paths += [p for p in (ROOT/'tests'/folder).rglob('*') if p.is_file()]
    browser = ROOT/'tests/wenku8_browser'
    paths += list(browser.glob('*.py')) + list(browser.glob('*.md')) + list(browser.glob('*.txt'))
    for folder in ('online_search_20261018','selected_detail_20261018','logging_online_review_20261018','chrome_regression_20261018'):
        paths += [p for p in (browser/folder).rglob('*') if p.is_file()]
    before = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (out/'legacy_baseline.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    env = {**os.environ, 'BOOKLIST_ENV':'test', 'BOOKLIST_DATA_DIR':str(data), 'PYTHONIOENCODING':'utf-8',
        'TEMP':str(ROOT/'.runtime/tmp'), 'TMP':str(ROOT/'.runtime/tmp')}
    results = {}

    def command(name, args, extra=None):
        run = subprocess.run([sys.executable,'-B',*args],env={**env,**(extra or {})},cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace',timeout=120)
        (out/(name+'.log')).write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
        results[name]={'exit_code':run.returncode}
        print(name,run.returncode,flush=True)
        if run.returncode:
            raise RuntimeError(name+' failed; inspect its log')

    try:
        command('pip_check',['-m','pip','check'])
        command('system_check',['manage.py','check'])
        command('migrate_first',['manage.py','migrate','--noinput'])
        command('migrate_second',['manage.py','migrate','--noinput'])
        command('migration_drift',['manage.py','makemigrations','--check','--dry-run'])
        password='Init-'+secrets.token_urlsafe(24)+'!'
        command('init_admin_first',['manage.py','init_admin','owner'],{'BOOKLIST_INITIAL_PASSWORD':password})
        command('init_admin_second',['manage.py','init_admin','owner'],{'BOOKLIST_INITIAL_PASSWORD':'ignored'})
        command('foundation_tests',['manage.py','test','booklist.tests','--noinput','-v','2'])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        log=(out/'http_server.log').open('w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,'-B','manage.py','runserver',f'127.0.0.1:{port}','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            base=f'http://127.0.0.1:{port}'
            opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def request(path,payload=None,csrf=None):
                headers={}
                if payload is not None:
                    headers['Content-Type']='application/json'
                if csrf:
                    headers['X-CSRFToken']=csrf
                req=Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
                try:
                    with opener.open(req,timeout=5) as res:
                        return res.status,json.loads(res.read())
                except HTTPError as exc:
                    return exc.code,json.loads(exc.read())

            ready=False
            for _ in range(50):
                try:
                    code,_=request('/health/')
                    if code==200:
                        ready=True
                        break
                except (URLError,ConnectionError):
                    pass
                if server.poll() is not None:
                    break
                time.sleep(.1)
            assert ready,'local HTTP server not ready'
            checks={'health':ready}
            checks['csrf_enforced']=request('/api/v1/identity/',{})[0]==403
            csrf=request('/api/v1/csrf/')[1]['csrf_token']
            checks['identity_issued']=request('/api/v1/identity/',{},csrf)[0]==200
            checks['login']=request('/api/v1/admin/login/',{'username':'owner','password':password},csrf)[0]==200
            checks['admin_me']=request('/api/v1/admin/me/')[1]=={'username':'owner'}
            csrf=request('/api/v1/csrf/')[1]['csrf_token']
            checks['last_admin_protected']=request('/api/v1/admin/accounts/1/disable/',{},csrf)[0]==409
            checks['logout']=request('/api/v1/admin/logout/',{},csrf)[0]==200
            checks['old_session_rejected']=request('/api/v1/admin/me/')[0]==401
            results['real_http']=checks
            assert all(checks.values())
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            log.close()
        source=sqlite3.connect(str(data/'booklist.sqlite3'))
        restored=sqlite3.connect(str(out/'restored.sqlite3'))
        source.backup(restored)
        checks={'integrity':restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok',
            'foreign_keys':restored.execute('PRAGMA foreign_key_check').fetchall()==[],
            'one_admin':restored.execute('SELECT count(*) FROM booklist_admin').fetchone()[0]==1,
            'password_not_plaintext':password not in str(restored.execute('SELECT * FROM booklist_admin').fetchall()),
            'audit_retained':restored.execute('SELECT count(*) FROM booklist_adminauditlog').fetchone()[0]>=2,
            'triggers_retained':restored.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]>=40}
        results['local_backup_readback']=checks
        assert all(checks.values())
        source.close()
        restored.close()
        results['success']=True
    except Exception as exc:
        results['success']=False
        results['error_class']=type(exc).__name__
        results['failed_step']=str(exc) if type(exc) in (RuntimeError,AssertionError) else 'Inspect logs'
    changed=[p for p,digest in before.items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=digest]
    results['protected_legacy_files']=len(before)
    results['changed_legacy_files']=changed
    results['sqlite_version']=sqlite3.sqlite_version
    results['scope']='local only; no source-site requests, paid API or production deployment'
    results['success']=results['success'] and not changed
    (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('EVIDENCE:',out,flush=True)
    return 0 if results['success'] else 1


if __name__=='__main__':
    raise SystemExit(main())
