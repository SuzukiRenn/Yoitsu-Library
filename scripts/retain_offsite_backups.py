"""Bounded local offsite retention. Preview by default; apply only after a fresh verified backup."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

NAME = re.compile(r'booklist-(\d{8}T\d{6}Z)-[a-f0-9]{8}\.tar')
UTC = timezone.utc


def safe_root(directory):
    root = Path(directory).absolute()
    for part in (root, *root.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('linked_backup_directory')
    if not root.is_dir():
        raise ValueError('backup_directory_missing')
    return root.resolve(strict=True)


def fingerprint(path, root):
    if path.parent != root or path.resolve(strict=True).parent != root:
        raise ValueError('file_outside_backup_directory')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('linked_or_nonregular_backup_file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('oversized_receipt')
    return json.loads(path.read_text(encoding='utf-8-sig'))


@contextmanager
def guard(root):
    path = root / '.retention.lock'
    if path.exists() or path.is_symlink():
        fingerprint(path, root)
    with path.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def inventory(root):
    valid, protected = [], []
    for archive in sorted(root.iterdir()):
        match = NAME.fullmatch(archive.name)
        if not match:
            continue  # Partial files and unknown names are never cleanup candidates.
        paths = [archive, root / (archive.name + '.receipt.json'), archive.with_suffix('.verified.json')]
        try:
            stamps = [fingerprint(p, root) for p in paths]
            receipt, verified = [read_json(p) for p in paths[1:]]
            when = datetime.strptime(match[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
            expected = receipt['sha256']
            if (receipt.get('status') != 'snapshot_ready'
                    or verified.get('status') != 'offsite_verified'
                    or any(verified.get(key) != receipt.get(key) for key in ('filename', 'bytes', 'sha256', 'release'))
                    or receipt.get('filename') != archive.name
                    or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)
                    or receipt.get('bytes') != stamps[0][2]
                    or verified.get('all_file_hashes_verified') is not True
                    or verified.get('isolated_restore_verified') is not True
                    or verified.get('database_check') != 'ok'
                    or verified.get('foreign_key_errors') != 0
                    or digest(archive) != expected):
                raise ValueError('backup_or_receipt_validation_failed')
            if stamps != [fingerprint(p, root) for p in paths]:
                raise ValueError('backup_changed_during_scan')
            valid.append({'name': archive.name, 'when': when, 'paths': paths, 'stamps': stamps,
                          'sha256': expected, 'bytes': sum(s[2] for s in stamps)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            protected.append({'name': archive.name, 'reason': type(exc).__name__ + ': ' + str(exc)})
    return sorted(valid, key=lambda b: (b['when'], b['name']), reverse=True), protected


def choose(valid, now):
    kept, expired, buckets = [], [], set()
    for index, backup in enumerate(valid):
        age = now - backup['when']
        if age <= timedelta(days=7):
            reason = 'last_7_days'
        elif age <= timedelta(days=35):
            bucket = (age - timedelta(days=7)) // timedelta(days=7)
            # Exactly 14/21/28/35-day boundaries belong to the preceding weekly window.
            if (age - timedelta(days=7)) % timedelta(days=7) == timedelta(0):
                bucket -= 1
            if bucket in buckets:
                expired.append(backup); continue
            buckets.add(bucket); reason = 'weekly_' + str(bucket + 1)
        elif index == 0:
            reason = 'latest_verified'
        else:
            expired.append(backup); continue
        kept.append(dict(backup, reason='latest_verified' if index == 0 else reason))
    return kept, expired


def run(directory, *, apply=False, after_backup=None, now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('timezone_required')
    root = safe_root(directory)
    with guard(root):
        valid, protected = inventory(root)
        kept, expired = choose(valid, now)
        result = {'mode': 'apply' if apply else 'preview', 'directory': str(root),
                  'policy': {'keep_all_days': 7, 'older_weekly_windows': 4, 'always_keep_latest_verified': True},
                  'kept': [{'name': b['name'], 'reason': b['reason']} for b in kept],
                  'delete': [{'name': b['name'], 'files': [str(p) for p in b['paths']], 'bytes': b['bytes']} for b in expired],
                  'protected': protected, 'reclaimable_bytes': sum(b['bytes'] for b in expired), 'deleted': []}
        if not apply:
            return result
        if not after_backup or not valid:
            raise ValueError('fresh_verified_backup_required')
        anchor = Path(after_backup).absolute()
        if anchor != valid[0]['paths'][0] or not timedelta(0) <= now - valid[0]['when'] <= timedelta(days=1):
            raise ValueError('anchor_must_be_latest_fresh_verified_backup')
        # Recheck actual content and isolated restore now; do not trust a historical JSON marker alone.
        from verify_offsite_backup import verify
        verify(anchor, read_json(valid[0]['paths'][1]))
        log = root / '.retention-history.jsonl'
        if log.exists() or log.is_symlink():
            fingerprint(log, root)
        def record(event):
            with log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(time=now.isoformat(), **event), ensure_ascii=True) + '\n')
                stream.flush(); os.fsync(stream.fileno())
        record({'event': 'plan', 'anchor': anchor.name, **result})
        for backup in expired:
            # Recheck kept copies and candidate immediately before deleting this exact three-file group.
            for item in [*kept, backup]:
                if item['stamps'] != [fingerprint(p, root) for p in item['paths']] or digest(item['paths'][0]) != item['sha256']:
                    raise ValueError('backup_changed_before_delete')
            for path in backup['paths']:
                path.unlink()
            result['deleted'].append(backup['name'])
            record({'event': 'deleted', 'name': backup['name'], 'files': [str(p) for p in backup['paths']]})
        record({'event': 'complete', 'deleted_count': len(result['deleted'])})
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--after-backup')
    args = parser.parse_args()
    print(json.dumps(run(args.directory, apply=args.apply, after_backup=args.after_backup), ensure_ascii=True))
