param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21',
    [string]$Destination='',
    [string]$IdentityFile=''
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
if(!$Destination){$Destination=Join-Path $repoRoot 'backups/server'}
$Destination=[IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
# Keep server environment secrets and certificate private keys out of shared ACLs/Git.
$taskIdentity=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $Destination /inheritance:r /grant:r "*${taskIdentity}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot protect local backup directory'}
$taskOptions=@('-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3')
if($IdentityFile){$taskOptions+=@('-i',[IO.Path]::GetFullPath($IdentityFile),'-o','IdentitiesOnly=yes','-o','BatchMode=yes')}
$taskScript=[IO.File]::ReadAllText((Join-Path $repoRoot 'deploy/offsite_snapshot.py')).Replace("`r`n","`n")+"`n"
Write-Host '1/3 Creating a consistent server snapshot (SSH password if requested).'
$taskRemote="flock -w 30 /opt/shared-booklist/deploy.lock python3 -"
if($IdentityFile){$taskOutput=@(& ssh @taskOptions "root@$ServerHost" snapshot 2>&1)}
else{$taskOutput=@($taskScript | & ssh @taskOptions "root@$ServerHost" $taskRemote 2>&1)}
if($LASTEXITCODE -ne 0){$taskOutput | ForEach-Object {Write-Host ([string]$_)};throw 'Server snapshot failed; no local backup marked successful'}
$taskReceipt=($taskOutput | Select-Object -Last 1) | ConvertFrom-Json
if($taskReceipt.status -ne 'snapshot_ready' -or $taskReceipt.filename -notmatch '^booklist-[0-9TZ]+-[a-f0-9]{8}\.tar$' -or $taskReceipt.sha256 -notmatch '^[a-f0-9]{64}$'){throw 'Invalid snapshot receipt'}
$taskFile=Join-Path $Destination $taskReceipt.filename
if(Test-Path -LiteralPath $taskFile){throw 'Refusing to overwrite a previous backup'}
$taskPending=$taskFile+'.partial'
Write-Host '2/3 Downloading the snapshot to this computer.'
if($IdentityFile){
 $taskStart=[Diagnostics.ProcessStartInfo]::new((Get-Command ssh).Source)
 foreach($taskArgument in ($taskOptions+@("root@$ServerHost","download $($taskReceipt.filename)"))){$taskStart.ArgumentList.Add($taskArgument)}
 $taskStart.UseShellExecute=$false;$taskStart.RedirectStandardOutput=$true;$taskStart.RedirectStandardError=$true;$taskStart.RedirectStandardInput=$true
 $taskProcess=[Diagnostics.Process]::Start($taskStart);$taskProcess.StandardInput.Close()
 $taskStderr=$taskProcess.StandardError.ReadToEndAsync()
 $taskStream=[IO.File]::Open($taskPending,[IO.FileMode]::CreateNew)
 try{$taskProcess.StandardOutput.BaseStream.CopyTo($taskStream)}finally{$taskStream.Dispose()}
 $taskProcess.WaitForExit()
 if($taskProcess.ExitCode -ne 0){throw "Download failed: $($taskStderr.GetAwaiter().GetResult())"}
}else{
 & scp @taskOptions "root@${ServerHost}:/var/lib/shared-booklist/offsite-export/$($taskReceipt.filename)" $taskPending
 if($LASTEXITCODE -ne 0){throw 'Download incomplete; partial file preserved for diagnosis'}
}
if((Get-FileHash -LiteralPath $taskPending -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskReceipt.sha256){throw 'Download SHA256 mismatch'}
Move-Item -LiteralPath $taskPending -Destination $taskFile
$taskReceiptPath=$taskFile+'.receipt.json'
$taskReceipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $taskReceiptPath -Encoding utf8
Write-Host '3/3 Verifying every file and restoring the database into temporary storage.'
& (Join-Path $repoRoot '.venv/Scripts/python.exe') (Join-Path $repoRoot 'scripts/verify_offsite_backup.py') $taskFile $taskReceiptPath
if($LASTEXITCODE -ne 0){throw 'Isolated restore verification failed'}
Write-Host "OFFSITE_BACKUP_VERIFIED $taskFile" -ForegroundColor Green
if($IdentityFile){
 & ssh @taskOptions "root@$ServerHost" "ack $($taskReceipt.filename) $($taskReceipt.sha256)"
 if($LASTEXITCODE -ne 0){Write-Warning 'Local backup verified; remote export cleanup needs attention'}
}
