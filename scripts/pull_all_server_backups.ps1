param(
 [string]$ServerHost='123.56.222.21',
 [string]$IdentityFile='.runtime/operations_20261021/offsite_ed25519'
)
$ErrorActionPreference='Stop'
$failures=@()
foreach($edition in @('anonymous','identity')){
 try { & (Join-Path $PSScriptRoot 'pull_server_backup.ps1') -ServerHost $ServerHost -IdentityFile $IdentityFile -Edition $edition }
 catch { $failures += $edition; Write-Warning ("Backup needs attention: " + $edition + "; " + $_.Exception.Message) }
}
if($failures.Count){throw ("Backup incomplete: " + ($failures -join ', '))}
