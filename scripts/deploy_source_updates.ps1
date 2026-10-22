param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21',
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Release)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot 'predeploy.ps1') -ServerHost $ServerHost -Release $Release
Write-Host 'Release deployed. Enter SSH password again for source preview and daily timer setup.' -ForegroundColor Cyan
$taskBootstrap=@'
import pathlib,subprocess
release=pathlib.Path('/opt/shared-booklist/current').resolve(strict=True)
subprocess.run(['python3',str(release/'deploy/install_source_updates.py')],check=True)
'@
$taskCode=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskBootstrap.Replace("`r`n","`n")))
$taskCommand="python3 -c `"import base64;exec(base64.b64decode('$taskCode'))`""
& ssh -T -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 "root@$ServerHost" $taskCommand 2>&1 | Tee-Object -FilePath (Join-Path $taskRoot '.runtime/source_update_preflight/deploy.log')
if($LASTEXITCODE -ne 0){throw 'Daily update setup stopped. Inspect the safe server logs before retrying.'}
