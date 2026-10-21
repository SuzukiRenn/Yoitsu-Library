"""Read-only Linux inventory; execute via SSH stdin, without credentials or host identifiers in output."""
import json
import os
import platform
import re
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


def deployment_status(expected_release):
    """Inspect only this project's containers; never emit configuration or raw logs."""
    def read(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout.strip()

    result = {'expected_release': expected_release, 'passed': False, 'services': {}}
    try:
        checkout = '/opt/shared-booklist/releases/'+expected_release
        migration_paths = read(['git', '-C', checkout, 'ls-tree', '-r', '--name-only',
            expected_release, 'booklist/migrations']).splitlines()
        expected_migrations = sorted(path.rsplit('/', 1)[1][:-3] for path in migration_paths
            if re.fullmatch(r'booklist/migrations/\d{4}_[a-z0-9_]+\.py', path))
        if not expected_migrations:
            raise ValueError('Release contains no migrations')
        result['expected_migration'] = expected_migrations[-1]
        for service in ('app', 'scheduler', 'backup'):
            identifiers = read(['docker', 'ps', '-aq', '--filter',
                'label=com.docker.compose.project=shared-booklist-predeploy', '--filter',
                'label=com.docker.compose.service='+service, '--filter',
                'label=com.docker.compose.oneoff=False']).splitlines()
            if len(identifiers) != 1:
                result['services'][service] = {'passed': False, 'container_count': len(identifiers)}
                continue
            identifier = identifiers[0]
            def inspect(field):
                return json.loads(read(['docker', 'inspect', '--format', '{{json '+field+'}}', identifier]))
            state = inspect('.State')
            item = {'running': state['Running'], 'restart_count': inspect('.RestartCount'),
                'image': inspect('.Config.Image'),
                'ports_published': bool(inspect('.HostConfig.PortBindings')),
                'health': state.get('Health', {}).get('Status')}
            item['passed'] = (item['running'] and item['restart_count'] == 0
                and not item['ports_published'] and item['image'] == 'shared-booklist:'+expected_release)
            if service == 'app':
                item['passed'] = item['passed'] and item['health'] == 'healthy'
                probe = ("import json,platform,sqlite3;"
                    "c=sqlite3.connect('file:/var/lib/shared-booklist/booklist.sqlite3?mode=ro',uri=True,timeout=5);"
                    "c.execute('PRAGMA query_only=ON');"
                    "print(json.dumps({'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,"
                    "'migration':c.execute(\"SELECT name FROM django_migrations WHERE app='booklist' ORDER BY id DESC LIMIT 1\").fetchone()[0]}));c.close()")
                runtime = json.loads(read(['docker', 'exec', identifier, 'python', '-c', probe]))
                result['runtime'] = runtime
                item['passed'] = item['passed'] and runtime == {'python':'3.14.8', 'sqlite':'3.53.2',
                    'migration':expected_migrations[-1]}
            else:
                logs = subprocess.run(['docker', 'logs', '--tail', '30', identifier],
                    capture_output=True, text=True, timeout=10, check=True)
                lines = (logs.stdout+'\n'+logs.stderr).splitlines()
                if service == 'scheduler':
                    item['recent_success'] = 'scheduler_ok' in lines
                else:
                    records = []
                    for line in lines:
                        try: records.append(json.loads(line))
                        except json.JSONDecodeError: pass
                    item['recent_success'] = any(isinstance(record, dict)
                        and record.get('status') == 'verified' and record.get('restore_drill') is True
                        for record in records)
                item['passed'] = item['passed'] and item['recent_success']
            result['services'][service] = item
        result['network_internal'] = read(['docker', 'network', 'inspect', '--format',
            '{{.Internal}}', 'shared-booklist-predeploy_private']) == 'true'
        with open('/var/lib/shared-booklist/predeploy/release.txt', encoding='ascii') as stream:
            result['release_matches'] = stream.read().strip() == expected_release
        result['current_matches'] = os.path.realpath('/opt/shared-booklist/current') == '/opt/shared-booklist/releases/'+expected_release
        result['passed'] = (result['network_internal'] and result['release_matches'] and result['current_matches']
            and all(item['passed'] for item in result['services'].values()))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as error:
        result['error'] = type(error).__name__
    return result


def main():
    expected_release = sys.argv[1] if len(sys.argv) > 1 else None
    if expected_release and not re.fullmatch('[a-f0-9]{40}', expected_release):
        raise SystemExit('Invalid expected release')
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
    holders=[]
    if os.path.exists('/proc'):
        for pid in os.listdir('/proc'):
            if not pid.isdecimal(): continue
            try:
                if os.readlink('/proc/'+pid+'/fd/9') == '/opt/shared-booklist/deploy.lock':
                    with open('/proc/'+pid+'/comm',encoding='ascii') as stream:
                        item={'pid':int(pid),'process':stream.read().strip()}
                    with open('/proc/'+pid+'/stat',encoding='ascii') as stream:
                        fields=stream.read().rpartition(')')[2].split()
                    with open('/proc/uptime',encoding='ascii') as stream:
                        uptime=float(stream.read().split()[0])
                    item.update(parent_pid=int(fields[1]),state=fields[0],age_seconds=int(uptime-int(fields[19])/os.sysconf('SC_CLK_TCK')))
                    holders.append(item)
            except (OSError, UnicodeError):
                pass
    result = {'schema_version':1, 'scope':'read_only_no_installs_no_deployment',
        'os':release, 'architecture':platform.machine(), 'python':list(sys.version_info[:3]),
        'sqlite':sqlite3.sqlite_version, 'cpu_count':os.cpu_count(), 'memory':memory,
        'disk_root_bytes':{'total':disk.total,'free':disk.free}, 'loopback_listeners':ports,
        'tools':{name:bool(shutil.which(name)) for name in ('docker','podman','nginx','git','curl','openssl','systemctl','gcc','make')},
        'docker':command(['docker','--version']), 'compose':command(['docker','compose','version','--short']),
        'existing_paths':{path:os.path.exists(path) for path in ('/opt/shared-booklist','/srv/shared-booklist','/var/lib/shared-booklist')},
        'deployment_fd_holders':holders,
        'project_containers':command(['docker','ps','-a','--filter','label=com.docker.compose.project=shared-booklist-predeploy','--format','{{.Names}}|{{.Status}}|{{.Ports}}']),
        'predeploy_database_exists':os.path.isfile('/var/lib/shared-booklist/predeploy/data/booklist.sqlite3')}
    if expected_release:
        result['deployment_verification'] = deployment_status(expected_release)
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
