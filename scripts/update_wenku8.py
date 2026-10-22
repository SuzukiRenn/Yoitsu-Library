"""Daily source metadata worker. Authentication is provided by a private file."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=Path('/run/source-credentials.json'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from booklist.source_updates import run
    result = run(args.credentials, dry_run=args.dry_run)
    # This report contains public book metadata and safe error codes only.
    print(json.dumps(result, ensure_ascii=True), flush=True)
    if result['status'] == 'needs_attention':
        return 1
    return 2 if args.dry_run and result['status'] == 'network_wait' else 0


if __name__ == '__main__':
    raise SystemExit(main())
