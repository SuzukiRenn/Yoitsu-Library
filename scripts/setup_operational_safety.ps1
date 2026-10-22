param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskPrivate=Join-Path $taskRoot '.runtime/operations_20261021'
New-Item -ItemType Directory -Force -Path $taskPrivate | Out-Null
$taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $taskPrivate /inheritance:r /grant:r "*${taskSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot protect operations directory'}
$taskKey=Join-Path $taskPrivate 'offsite_ed25519'
if(!(Test-Path -LiteralPath $taskKey)){
 $taskStart=[Diagnostics.ProcessStartInfo]::new((Get-Command ssh-keygen).Source)
 foreach($taskArgument in @('-t','ed25519','-N','','-C','shared-booklist-offsite-only','-f',$taskKey)){$taskStart.ArgumentList.Add($taskArgument)}
 $taskStart.UseShellExecute=$false
 $taskProcess=[Diagnostics.Process]::Start($taskStart);$taskProcess.WaitForExit()
 if($taskProcess.ExitCode -ne 0){throw 'Restricted backup key generation failed'}
}
if(!(Test-Path -LiteralPath ($taskKey+'.pub'))){throw 'Backup public key missing'}
$taskFiles=@{}
foreach($taskFile in @('dns_hook.py','manage_renewal.py','offsite_snapshot.py')){
 $taskFiles[$taskFile]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $taskRoot "deploy/$taskFile")))
}
$taskPayload=@{files=$taskFiles;public_key=([IO.File]::ReadAllText($taskKey+'.pub').Trim())}|ConvertTo-Json -Compress
$taskPayloadBase64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskPayload))
$taskBootstrap=@'
import base64, hashlib, json, os, pathlib, subprocess, time
payload=json.loads(base64.b64decode('PAYLOAD_PLACEHOLDER'))
root=pathlib.Path('/etc/shared-booklist/renewal')
root.mkdir(mode=0o700,exist_ok=True);root.chmod(0o700)
for name,encoded in payload['files'].items():
    target=(root/name) if name!='offsite_snapshot.py' else pathlib.Path('/etc/shared-booklist/offsite_snapshot.py')
    if target.is_symlink():raise RuntimeError('linked_operator_tool')
    raw=base64.b64decode(encoded)
    compile(raw,str(target),'exec')
    if target.exists() and target.read_bytes()!=raw:
        backup=target.with_name(target.name+'.before-'+str(time.time_ns()))
        backup.write_bytes(target.read_bytes());backup.chmod(0o600)
    target.write_bytes(raw);target.chmod(0o600)
public=payload['public_key'].split()
if len(public)!=3 or public[0]!='ssh-ed25519' or public[2]!='shared-booklist-offsite-only':raise RuntimeError('unexpected_backup_key')
ssh=pathlib.Path('/root/.ssh');ssh.mkdir(mode=0o700,exist_ok=True)
authorized=ssh/'authorized_keys'
if authorized.is_symlink():raise RuntimeError('linked_authorized_keys')
line='restrict,command="/usr/bin/flock -w 30 /opt/shared-booklist/deploy.lock /usr/bin/python3 /etc/shared-booklist/offsite_snapshot.py --ssh-dispatch" '+payload['public_key']
existing=authorized.read_text() if authorized.exists() else ''
matching=[entry for entry in existing.splitlines() if public[1] in entry]
if matching and matching!=[line]:raise RuntimeError('backup_key_already_has_different_permissions')
if not matching:
    with authorized.open('a') as stream:stream.write(('\n' if existing and not existing.endswith('\n') else '')+line+'\n')
authorized.chmod(0o600)
print('OPERATIONAL_TOOLS_INSTALLED; backup key is forced-command/restrict only',flush=True)
# Read-only diagnostics: a previous SSH maintenance shell may still own the lock.
result=subprocess.run(['lslocks','--noheadings','--output','PID,COMMAND,PATH'],capture_output=True,text=True)
for item in result.stdout.splitlines():
    if '/opt/shared-booklist/deploy.lock' in item:
        print('DEPLOY_LOCK '+item.strip(),flush=True)
        pid=int(item.split()[0])
        for stat in pathlib.Path('/proc').glob('[0-9]*/stat'):
            try:
                fields=stat.read_text().split()
                if int(fields[0])==pid or int(fields[3])==pid:
                    cmd=(stat.parent/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                    print('LOCK_PROCESS '+str(fields[0])+' parent='+fields[3]+' command='+cmd[:200],flush=True)
            except (OSError,ValueError):pass
lock=pathlib.Path('/opt/shared-booklist/deploy.lock').stat()
holders=[]
for process in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        descriptor=(process/'fd/9').stat()
        if (descriptor.st_dev,descriptor.st_ino)!=(lock.st_dev,lock.st_ino):continue
        fields=(process/'stat').read_text().split()
        args=(process/'cmdline').read_bytes().split(b'\0')
        args=[part.decode(errors='replace') for part in args if part]
        holders.append((int(process.name),int(fields[3]),args))
        print('OPEN_LOCK_DESCRIPTOR '+json.dumps({'pid':int(process.name),'parent':int(fields[3]),'args':args}),flush=True)
    except (OSError,ValueError):pass
# Diagnostics only. An operator must identify a previous maintenance session before
# stopping anything; a generic orphaned sleeper is not sufficient ownership proof.
'@
$taskBootstrap=$taskBootstrap.Replace('PAYLOAD_PLACEHOLDER',$taskPayloadBase64).Replace("`r`n","`n")+"`n"
$taskSsh=@('-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no','-o','ClearAllForwardings=yes')
Write-Host 'Enter SSH password to install isolated renewal tools and a restricted backup key.'
$taskBootstrap | & ssh @taskSsh "root@$ServerHost" 'python3 -' 2>&1 | Tee-Object (Join-Path $taskPrivate 'install.log')
if($LASTEXITCODE -ne 0){throw 'Tools installation failed'}
Write-Host 'Tools installed. Continue certificate setup only after the deployment lock is clear.'
