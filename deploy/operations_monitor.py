"""Private host monitoring: record meaningful changes; no external email until a sender is configured."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

STATE = Path('/var/lib/shared-booklist/operations-monitor')
RECIPIENT = 'kuroneko0217@outlook.com'
PROFILES = {
    'anonymous': {'project':'shared-booklist-predeploy','state':Path('/var/lib/shared-booklist/predeploy'),
                  'offsite':Path('/var/lib/shared-booklist/offsite-export'),'port':8443},
    'identity': {'project':'shared-booklist-identity','state':Path('/var/lib/shared-booklist-identity'),
                 'offsite':Path('/var/lib/shared-booklist-identity/offsite-export'),'port':8444},
}


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=25)


def backup_findings(kind, profile, now):
    findings=[]
    try:
        health=json.loads((profile['state']/'backups/backup-health.json').read_text())
        when=datetime.fromisoformat(health['checked_at'])
        if health.get('status')!='verified' or now-when>timedelta(hours=8):
            findings.append(kind+':backup_failed_or_stale')
    except (OSError,ValueError,KeyError,TypeError):
        findings.append(kind+':backup_status_unavailable')
    try:
        receipt=json.loads((profile['offsite']/'last-verified.json').read_text())
        if receipt.get('edition')!=kind or now-datetime.fromisoformat(receipt['verified_at'])>timedelta(hours=48):
            findings.append(kind+':offsite_backup_overdue')
    except (OSError,ValueError,KeyError,TypeError):
        findings.append(kind+':offsite_backup_unconfirmed')
    return findings


def service_findings(kind, profile):
    try:
        ids=command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+profile['project']]).split()
        rows=json.loads(command(['docker','inspect',*ids])) if ids else []
        states={r['Config']['Labels'].get('com.docker.compose.service'):r['State'] for r in rows
                if not r['Config']['Labels'].get('com.docker.compose.oneoff','false').lower()=='true'}
        bad=[name for name in ('app','scheduler','backup','proxy') if name not in states
             or states[name].get('Status')!='running' or states[name].get('Health',{}).get('Status','healthy')!='healthy']
        return [kind+':service_unhealthy:'+name for name in bad]
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        return [kind+':container_probe_failed']


def collect(now=None):
    now=now or datetime.now(timezone.utc)
    findings=[]
    for name, profile in PROFILES.items():
        try:
            disk=shutil.disk_usage(profile['state'])
            if disk.free<3*1024**3 or disk.free/disk.total<.10:findings.append(name+':disk_space_low')
        except OSError:findings.append(name+':disk_probe_failed')
        findings+=backup_findings(name,profile,now)+service_findings(name,profile)
        port=profile['port']
        try:
            command(['curl','--noproxy','*','--max-time','15','--fail','--silent','--show-error',
                     '--resolve',f'sharedbooklist.xyz:{port}:127.0.0.1',f'https://sharedbooklist.xyz:{port}/health/'])
        except (OSError,subprocess.SubprocessError):findings.append(name+':https_health_failed')
    try:
        command(['openssl','x509','-in','/etc/shared-booklist/ssh-access/tls/fullchain.pem','-noout','-checkend','1209600'])
    except (OSError,subprocess.SubprocessError):findings.append('shared:certificate_expires_within_14_days_or_invalid')
    return {'checked_at':now.isoformat(),'status':'needs_attention' if findings else 'healthy',
            'findings':sorted(set(findings)), 'notification':{'email_enabled':False,'recipient':RECIPIENT,'reason':'smtp_not_configured'}}


def save(result, root=STATE):
    for part in (root,*root.parents):
        if part.is_symlink():raise ValueError('linked_monitor_directory')
    root.mkdir(parents=True,exist_ok=True,mode=0o700);root.chmod(0o700)
    state=root/'status.json';events=root/'events.jsonl'
    if state.is_symlink() or events.is_symlink():raise ValueError('linked_monitor_file')
    previous={}
    if state.exists():
        try:previous=json.loads(state.read_text())
        except ValueError:pass
    if previous.get('findings')!=result['findings']:
        with events.open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(result,event='changed' if result['findings'] else 'recovered'))+'\n')
        events.chmod(0o600)
    pending=root/'status.tmp'
    if pending.is_symlink():raise ValueError('linked_monitor_temporary_file')
    pending.write_text(json.dumps(result,indent=2),encoding='utf-8');pending.chmod(0o600);os.replace(pending,state)
    return result


if __name__=='__main__':
    import fcntl
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (STATE/'monitor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps(save(collect())))
