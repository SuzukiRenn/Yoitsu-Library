param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21', [switch]$UseSavedCredentials)
$ErrorActionPreference='Stop'
$taskRepo=Split-Path $PSScriptRoot -Parent
$taskOutput=Join-Path $taskRepo '.runtime/source_update_preflight'
New-Item -ItemType Directory -Force -Path $taskOutput | Out-Null
$taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $taskOutput /inheritance:r /grant:r "*${taskSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot protect source session diagnostics'}
Write-Host 'Read-only Wenku8 session validation. Values are handled locally and are not echoed remotely.' -ForegroundColor Cyan
$taskUsername=$null;$taskPassword=$null
if(!$UseSavedCredentials){
 $taskUsername=Read-Host 'Wenku8 username' -AsSecureString
 $taskPassword=Read-Host 'Wenku8 password' -AsSecureString
}
$taskModule=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $taskRepo 'integrations/wenku8/session.py')))
$taskBootstrap=@'
import base64,json,os,pathlib,subprocess,sys,tempfile,time
def probe():
    if os.geteuid()!=0:raise RuntimeError('root_required')
    payload=json.load(sys.stdin)
    root=pathlib.Path('/etc/shared-booklist/source-updater')
    if root.is_symlink():raise RuntimeError('linked_source_configuration')
    root.mkdir(mode=0o750,exist_ok=True);os.chown(root,0,10001);root.chmod(0o750)
    module=root/'session.py'
    if module.is_symlink():raise RuntimeError('linked_source_module')
    raw=base64.b64decode(payload['module']);compile(raw,str(module),'exec')
    module.write_bytes(raw);module.chmod(0o644)
    credentials=root/'credentials.json'
    if credentials.is_symlink():raise RuntimeError('linked_source_credentials')
    values=payload.get('credentials')
    if values is not None:
        if set(values)!={'username','password'} or any(not isinstance(v,str) or not v or len(v)>300 for v in values.values()):raise RuntimeError('invalid_credentials')
        descriptor,temporary=tempfile.mkstemp(prefix='.credentials-',dir=root)
        try:
            with os.fdopen(descriptor,'w') as stream:
                json.dump(values,stream);stream.flush();os.fsync(stream.fileno())
            os.chown(temporary,0,10001);os.chmod(temporary,0o640);os.replace(temporary,credentials)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
    elif not credentials.is_file():raise RuntimeError('saved_credentials_missing')
    payload.clear();values=None
    state=pathlib.Path('/var/lib/shared-booklist/predeploy/data/source_updates')
    if state.is_symlink():raise RuntimeError('linked_source_state')
    state.mkdir(mode=0o700,exist_ok=True);os.chown(state,10001,10001);state.chmod(0o700)
    release=pathlib.Path('/opt/shared-booklist/current').resolve(strict=True)
    revision=subprocess.check_output(['git','-C',str(release),'rev-parse','HEAD'],text=True).strip()
    name='booklist-source-preflight-'+str(os.getpid())
    print('SOURCE_SESSION_PREFLIGHT_STARTED; only robots/login/update-list requests; no database mount',flush=True)
    try:
        subprocess.run(['docker','run','--rm','--name',name,'--network','bridge','--read-only','--user','10001:10001',
            '--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.25','--log-driver','none',
            '--tmpfs','/tmp:size=16m,mode=1777','-e','PYTHONPATH=/app',
            '-v',str(module)+':/opt/session.py:ro','-v',str(credentials)+':/run/source.json:ro',
            '-v',str(state)+':/state','shared-booklist:'+revision,
            'python','/opt/session.py','--state-dir','/state','--credentials','/run/source.json'],check=True,timeout=650,stderr=subprocess.STDOUT)
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
try:
    probe()
except BaseException as error:
    print('SOURCE_SESSION_NEEDS_ATTENTION '+type(error).__name__,flush=True)
    sys.exit(1)
'@
$taskCode=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskBootstrap.Replace("`r`n","`n")))
$taskCommand="python3 -c `"import base64;exec(base64.b64decode('$taskCode'))`""
$taskStart=[Diagnostics.ProcessStartInfo]::new((Get-Command ssh).Source)
foreach($taskArgument in @('-T','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',"root@$ServerHost",$taskCommand)){$taskStart.ArgumentList.Add($taskArgument)}
$taskStart.UseShellExecute=$false;$taskStart.RedirectStandardInput=$true;$taskStart.RedirectStandardOutput=$true
Write-Host 'Now enter the SSH password at its prompt. Source credentials are sent only through encrypted stdin.'
$taskProcess=[Diagnostics.Process]::Start($taskStart)
$taskPlainUsername=$null;$taskPlainPassword=$null;$taskPayload=$null
try{
 $taskPayloadValues=@{module=$taskModule}
 if(!$UseSavedCredentials){
  $taskPlainUsername=[Net.NetworkCredential]::new('',$taskUsername).Password
  $taskPlainPassword=[Net.NetworkCredential]::new('',$taskPassword).Password
  $taskPayloadValues.credentials=@{username=$taskPlainUsername;password=$taskPlainPassword}
 }
 $taskPayload=$taskPayloadValues|ConvertTo-Json -Depth 5 -Compress
 $taskProcess.StandardInput.Write($taskPayload);$taskProcess.StandardInput.Close()
}finally{
 $taskPlainUsername=$null;$taskPlainPassword=$null;$taskPayload=$null
 if($taskUsername){$taskUsername.Dispose()};if($taskPassword){$taskPassword.Dispose()}
}
$taskWriter=[IO.StreamWriter]::new((Join-Path $taskOutput 'probe.log'),$false,[Text.UTF8Encoding]::new($false))
try{
 while(($taskLine=$taskProcess.StandardOutput.ReadLine()) -ne $null){[Console]::WriteLine($taskLine);$taskWriter.WriteLine($taskLine);$taskWriter.Flush()}
}finally{$taskWriter.Dispose()}
$taskProcess.WaitForExit()
if($taskProcess.ExitCode -ne 0){throw 'Source session validation stopped; no database changes were made'}
Write-Host 'SOURCE_SESSION_PREFLIGHT_VERIFIED' -ForegroundColor Green
