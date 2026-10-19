"""Production checks with one deliberate exception: HSTS preload enrollment is not enabled."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
from django.core import checks

if settings.ENV != 'production': raise SystemExit('production_environment_required')
findings=checks.run_checks(include_deployment_checks=True)
blocked=[finding for finding in findings if finding.level >= checks.WARNING and finding.id != 'security.W021']
if blocked:
    print(json.dumps({'status':'blocked','checks':[finding.id for finding in blocked]}))
    raise SystemExit(1)
print(json.dumps({'status':'passed','acknowledged':[finding.id for finding in findings if finding.id == 'security.W021']}))
