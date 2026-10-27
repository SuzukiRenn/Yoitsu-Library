param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21')
$ErrorActionPreference='Stop'
$taskRepo=Split-Path $PSScriptRoot -Parent
$taskRuntime=Join-Path $taskRepo '.runtime/operations_20261021'
$taskKey=Join-Path $taskRuntime 'offsite_ed25519'
if(!(Test-Path -LiteralPath $taskKey)){throw 'Install the restricted backup key first'}
$taskRunner=Join-Path $taskRuntime 'scheduled_backup.ps1'
$taskTemplate=@'
$ErrorActionPreference='Stop'
Set-Location 'REPO_PLACEHOLDER'
$taskLog='.runtime/operations_20261021/scheduled-backup.log'
try {
 & ./scripts/pull_all_server_backups.ps1 -ServerHost 'HOST_PLACEHOLDER' -IdentityFile '.runtime/operations_20261021/offsite_ed25519' *>&1 | Tee-Object -FilePath $taskLog
} catch {
 [pscustomobject]@{time=(Get-Date).ToUniversalTime().ToString('o');status='needs_attention';reason=$_.Exception.Message}|ConvertTo-Json|Set-Content .runtime/operations_20261021/pc-backup-status.json
 $_.Exception.Message | Add-Content $taskLog
 exit 1
}
[pscustomobject]@{time=(Get-Date).ToUniversalTime().ToString('o');status='verified'}|ConvertTo-Json|Set-Content .runtime/operations_20261021/pc-backup-status.json
'@
$taskTemplate.Replace('REPO_PLACEHOLDER',$taskRepo.Replace("'","''")).Replace('HOST_PLACEHOLDER',$ServerHost)|Set-Content -LiteralPath $taskRunner -Encoding utf8
$taskAction=New-ScheduledTaskAction -Execute (Get-Command pwsh).Source -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$taskRunner+'"') -WorkingDirectory $taskRepo
$taskTriggers=@((New-ScheduledTaskTrigger -Daily -At '09:00'),(New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)))
$taskSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$taskPrincipal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$taskName='SharedBooklistOffsiteBackup'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Task already exists; inspect rather than overwrite it'}
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTriggers -Settings $taskSettings -Principal $taskPrincipal -Description 'Pull server snapshot to this PC; hash every member and independently restore DB. Runs only while this user is logged in.' | Select-Object TaskName,State
