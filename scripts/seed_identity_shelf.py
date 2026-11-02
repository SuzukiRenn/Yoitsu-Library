"""Explicit, checksum-bound replacement of identity shelf content."""
import argparse,hashlib,json,os,shutil,sys
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest',required=True);parser.add_argument('--sha256',required=True)
parser.add_argument('--preview-copy');parser.add_argument('--apply',action='store_true')
parser.add_argument('--expected-plan');parser.add_argument('--output',required=True)
args=parser.parse_args()
raw=Path(args.manifest).read_bytes()
assert hashlib.sha256(raw).hexdigest()==args.sha256,'Manifest checksum mismatch'
doc=json.loads(raw)
if args.preview_copy:
    assert not args.apply,'Preview cannot apply to live data'
    target=Path(os.environ['BOOKLIST_DATA_DIR'])/'booklist.sqlite3'
    assert not target.exists(),'Preview data directory must be fresh'
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(args.preview_copy,target)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from booklist.seed_identity import replace
result=replace(doc,args.sha256,expected_plan=args.expected_plan,preview=not args.apply)
out=Path(args.output);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
out.chmod(0o600)
print('IDENTITY_SEED_RESULT '+json.dumps(result,ensure_ascii=False))
