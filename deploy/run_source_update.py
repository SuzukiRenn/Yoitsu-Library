"""Root-owned daily launcher: fixed image, bounded job, no public ports."""
import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    release = Path('/opt/shared-booklist/current').resolve(strict=True)
    revision = subprocess.check_output(['git', '-C', str(release), 'rev-parse', 'HEAD'], text=True).strip()
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise RuntimeError('invalid_release')
    credentials = Path('/etc/shared-booklist/source-updater/credentials.json')
    info = credentials.lstat()
    if credentials.is_symlink() or info.st_uid != 0 or info.st_gid != 10001 or info.st_mode & 0o027:
        raise RuntimeError('source_credentials_permissions_invalid')
    command = ['docker', 'run', '--rm', '--name', 'booklist-source-update',
               '--network', 'bridge', '--read-only', '--user', '10001:10001',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--memory', '192m', '--cpus', '0.25', '--tmpfs', '/tmp:size=32m,mode=1777',
               '--log-driver', 'none', '--env-file', '/etc/shared-booklist/predeploy.env',
               '-v', '/var/lib/shared-booklist/predeploy/data:/var/lib/shared-booklist',
               '-v', str(credentials)+':/run/source-credentials.json:ro',
               'shared-booklist:'+revision, 'python', 'scripts/update_wenku8.py']
    if args.dry_run:
        command.append('--dry-run')
    def terminate(signum, frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM, terminate)
    # Only stop a container whose CID was created by this invocation. A failed
    # lock or name conflict must never stop another running worker.
    with tempfile.TemporaryDirectory(prefix='booklist-source-') as directory:
        cidfile = Path(directory)/'cid'
        command[2:2] = ['--cidfile', str(cidfile)]
        try:
            return subprocess.call(command)
        finally:
            if cidfile.is_file():
                cid = cidfile.read_text().strip()
                if re.fullmatch('[a-f0-9]{64}', cid):
                    subprocess.run(['docker', 'stop', '--time', '20', cid],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


if __name__ == '__main__':
    raise SystemExit(main())
