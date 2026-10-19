"""Offline verification. All preview contributions are synthetic test records."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).absolute().parents[1]


def main():
    out = ROOT/'.runtime/public_verification'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    env = {**os.environ, 'BOOKLIST_ENV': 'test', 'BOOKLIST_DATA_DIR': str(out/'data'), 'PYTHONIOENCODING': 'utf-8'}
    result = {'success': False, 'scope': 'isolated offline database; synthetic preview contributions; no production or source/AI calls'}
    commands = [
        ('system_check', ['check']), ('migrate', ['migrate', '--noinput']),
        ('migration_drift', ['makemigrations', '--check', '--dry-run']),
        ('all_tests', ['test', 'booklist.tests', '--noinput']),
        ('seed', ['import_wenku8', '--manifest', str(ROOT/'tests/fixtures/wenku8/manifest.json')]),
    ]
    try:
        for name, args in commands:
            run = subprocess.run([sys.executable, '-B', str(ROOT/'manage.py'), *args], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=240)
            (out/(name+'.log')).write_text(run.stdout+'\n'+run.stderr, encoding='utf-8')
            result[name] = run.returncode
            print(name, run.returncode, flush=True)
            if run.returncode:
                raise RuntimeError(name)
        run = subprocess.run(['node', str(ROOT/'scripts/test_public_ui.mjs')], cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        (out/'public_ui.log').write_text(run.stdout+'\n'+run.stderr, encoding='utf-8')
        if run.returncode:
            raise RuntimeError('public_ui')
        run = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/verify_public_migration.py')], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=60)
        (out/'migration_roundtrip.log').write_text(run.stdout+'\n'+run.stderr, encoding='utf-8')
        if run.returncode:
            raise RuntimeError('migration_roundtrip')
        result['success'] = True
    except Exception as exc:
        result['error_class'] = type(exc).__name__
    result['data_dir'] = str(out/'data')
    (out/'summary.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding='utf-8')
    print('EVIDENCE:', out)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
