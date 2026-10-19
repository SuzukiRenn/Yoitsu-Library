"""Offline stage-3 verification and isolated preview data; no source-site calls."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


ROOT = Path(__file__).absolute().parents[1]


def main():
    out = ROOT/'.runtime/catalog_verification'/(
        datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    env = {**os.environ, 'BOOKLIST_ENV': 'test', 'BOOKLIST_DATA_DIR': str(out/'data'), 'PYTHONIOENCODING': 'utf-8'}
    result = {'scope': 'offline tests and isolated preview DB; no source website, AI or production requests', 'success': False}
    try:
        for name, args in (
            ('system_check', ['check']), ('migrate', ['migrate', '--noinput']),
            ('migration_drift', ['makemigrations', '--check', '--dry-run']),
            ('all_tests', ['test', 'booklist.tests', '--noinput']),
            ('seed', ['import_wenku8', '--manifest', str(ROOT/'tests/fixtures/wenku8/manifest.json')]),
        ):
            run = subprocess.run([sys.executable, '-B', str(ROOT/'manage.py'), *args], cwd=ROOT,
                                 env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=180)
            (out/(name+'.log')).write_text(run.stdout+'\n'+run.stderr, encoding='utf-8')
            result[name] = run.returncode
            print(name, run.returncode, flush=True)
            if run.returncode:
                raise RuntimeError(name)
        result['success'] = True
    except Exception as exc:
        result['error_class'] = type(exc).__name__
    result['data_dir'] = str(out/'data')
    (out/'summary.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding='utf-8')
    print('EVIDENCE:', out)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
