param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21')
$ErrorActionPreference='Stop'
$taskRepo=Split-Path $PSScriptRoot -Parent
$taskDirectory=Join-Path $taskRepo '.runtime/operations_20261021'
New-Item -ItemType Directory -Force -Path $taskDirectory | Out-Null
$taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $taskDirectory /inheritance:r /grant:r "*${taskSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot protect local operations directory'}
Write-Host 'Input is handled locally. Neither value is sent through a remote terminal or command argument.' -ForegroundColor Cyan
$taskKey=Read-Host 'NEW RAM AccessKey ID' -AsSecureString
$taskSecret=Read-Host 'NEW RAM AccessKey Secret' -AsSecureString
$taskFiles=@{}
foreach($taskName in @('dns_hook.py','manage_renewal.py','offsite_snapshot.py')){
 $taskFiles[$taskName]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $taskRepo "deploy/$taskName")))
}
# Only this public bootstrap is placed in the SSH command. Secrets go to encrypted stdin.
$taskBootstrap=@'
import base64,json,os,pathlib,subprocess,sys,tempfile,time
def setup():
    if os.geteuid()!=0:raise RuntimeError('root_required')
    payload=json.load(sys.stdin)
    root=pathlib.Path('/etc/shared-booklist/renewal')
    for directory in [root,root.parent]:
        if directory.is_symlink():raise RuntimeError('linked_configuration_directory')
    root.mkdir(mode=0o700,exist_ok=True);root.chmod(0o700)
    for name in ['dns_hook.py','manage_renewal.py','offsite_snapshot.py']:
        target=(root/name) if name!='offsite_snapshot.py' else root.parent/name
        if target.is_symlink():raise RuntimeError('linked_operator_tool')
        raw=base64.b64decode(payload['files'][name]);compile(raw,str(target),'exec')
        if target.exists() and target.read_bytes()!=raw:
            old=target.with_name(target.name+'.before-'+str(time.time_ns()))
            old.write_bytes(target.read_bytes());old.chmod(0o600)
        target.write_bytes(raw);target.chmod(0o600)
    credentials=root/'credentials.json'
    if credentials.is_symlink():raise RuntimeError('linked_credentials')
    key=payload['credentials']['access_key_id'].strip()
    secret=payload['credentials']['access_key_secret'].strip()
    if not key or not secret:raise RuntimeError('empty_credentials')
    descriptor,temporary=tempfile.mkstemp(prefix='.credentials-',dir=root)
    try:
        with os.fdopen(descriptor,'w') as stream:
            json.dump({'access_key_id':key,'access_key_secret':secret},stream)
            stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600);os.replace(temporary,credentials)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    payload.clear();key=secret=None
    print('SECURE_CREDENTIALS_INSTALLED; starting staging renewal drill',flush=True)
    subprocess.run(['flock','-w','60','/opt/shared-booklist/deploy.lock','python3',str(root/'manage_renewal.py'),'setup'],check=True,timeout=1500)
try:
    setup()
except BaseException as error:
    # Never render payloads or source containing input in a traceback.
    print('SECURE_SETUP_NEEDS_ATTENTION '+type(error).__name__,file=sys.stderr,flush=True)
    sys.exit(1)
'@
$taskPublicCode=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskBootstrap.Replace("`r`n","`n")))
$taskRemote="python3 -c `"import base64;exec(base64.b64decode('$taskPublicCode'))`""
$taskStart=[Diagnostics.ProcessStartInfo]::new((Get-Command ssh).Source)
foreach($taskArgument in @('-T','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',"root@$ServerHost",$taskRemote)){
 $taskStart.ArgumentList.Add($taskArgument)
}
$taskStart.UseShellExecute=$false
$taskStart.RedirectStandardInput=$true
$taskStart.RedirectStandardOutput=$true
# OpenSSH reads its password directly from the local console. No remote PTY exists.
Write-Host 'Now enter the SSH password at the OpenSSH prompt. RAM values will not be echoed.'
$taskProcess=[Diagnostics.Process]::Start($taskStart)
$taskPlainKey=$null;$taskPlainSecret=$null;$taskPayload=$null
try{
 $taskPlainKey=[Net.NetworkCredential]::new('',$taskKey).Password
 $taskPlainSecret=[Net.NetworkCredential]::new('',$taskSecret).Password
 if(!$taskPlainKey.Trim() -or !$taskPlainSecret.Trim()){throw 'Empty RAM credential'}
 $taskPayload=@{files=$taskFiles;credentials=@{access_key_id=$taskPlainKey;access_key_secret=$taskPlainSecret}}|ConvertTo-Json -Compress -Depth 5
 $taskProcess.StandardInput.Write($taskPayload)
 $taskProcess.StandardInput.Close()
}finally{
 $taskPlainKey=$null;$taskPlainSecret=$null;$taskPayload=$null
 $taskKey.Dispose();$taskSecret.Dispose()
}
$taskLog=Join-Path $taskDirectory 'renewal-secure.log'
$taskWriter=[IO.StreamWriter]::new($taskLog,$false,[Text.UTF8Encoding]::new($false))
try{
 while(($taskLine=$taskProcess.StandardOutput.ReadLine()) -ne $null){
  [Console]::WriteLine($taskLine);$taskWriter.WriteLine($taskLine);$taskWriter.Flush()
 }
}finally{$taskWriter.Dispose()}
$taskProcess.WaitForExit()
if($taskProcess.ExitCode -ne 0){throw 'Certificate setup did not complete; inspect sanitized status, not credentials'}
Write-Host 'CERTIFICATE_AUTOMATION_VERIFIED' -ForegroundColor Green
