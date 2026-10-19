"""Read-only Linux inventory; execute via SSH stdin, without credentials or host identifiers in output."""
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys


def command(args):
    if not shutil.which(args[0]):
        return {'available': False}
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=8)
        return {'available': True, 'exit_code': result.returncode, 'version': result.stdout.strip()[:180]}
    except (OSError, subprocess.TimeoutExpired):
        return {'available': True, 'error': 'unavailable_or_timeout'}


def main():
    release = {}
    try:
        for line in open('/etc/os-release', encoding='utf-8'):
            key, _, value = line.partition('=')
            if key in ('ID', 'VERSION_ID'):
                release[key] = value.strip().strip('"')
    except OSError:
        pass
    memory = {}
    try:
        for line in open('/proc/meminfo', encoding='ascii'):
            key, _, value = line.partition(':')
            if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):
                memory[key+'_kib'] = int(value.split()[0])
    except OSError:
        pass
    ports = {}
    for port in (80,443,8000,8080,8765):
        with socket.socket() as connection:
            connection.settimeout(.2)
            ports[str(port)] = connection.connect_ex(('127.0.0.1',port)) == 0
    disk = shutil.disk_usage('/')
    result = {'schema_version':1, 'scope':'read_only_no_installs_no_deployment',
        'os':release, 'architecture':platform.machine(), 'python':list(sys.version_info[:3]),
        'sqlite':sqlite3.sqlite_version, 'cpu_count':os.cpu_count(), 'memory':memory,
        'disk_root_bytes':{'total':disk.total,'free':disk.free}, 'loopback_listeners':ports,
        'tools':{name:bool(shutil.which(name)) for name in ('docker','podman','nginx','git','curl','openssl','systemctl','gcc','make')},
        'docker':command(['docker','--version']), 'compose':command(['docker','compose','version','--short']),
        'existing_paths':{path:os.path.exists(path) for path in ('/opt/shared-booklist','/srv/shared-booklist','/var/lib/shared-booklist')}}
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
