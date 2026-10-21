param(
    [Parameter(Mandatory=$true)][string]$PreparationDirectory,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$names=@('compose.https.json','nginx.bootstrap.conf','nginx.https.conf','manifest.json')
$files=[ordered]@{}
foreach ($name in $names) {
    $files[$name]=[IO.File]::ReadAllText((Join-Path $PreparationDirectory $name))
}
$payload=[Text.Encoding]::UTF8.GetBytes(($files | ConvertTo-Json -Depth 10 -Compress))
$payload64=[Convert]::ToBase64String($payload)
$hash=[Security.Cryptography.SHA256]::Create()
try { $digest=([BitConverter]::ToString($hash.ComputeHash($payload))).Replace('-','').ToLowerInvariant() }
finally { $hash.Dispose() }
$code64=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $repoRoot 'deploy/stage_https.py')))
$remote=@"
set -eu
python3 - '$payload64' '$digest' <<'HTTPS_STAGE_PY'
import base64
exec(compile(base64.b64decode('$code64'), 'stage_https.py', 'exec'))
HTTPS_STAGE_PY
exit 0
"@
$log=Join-Path $PreparationDirectory 'server-staging.log'
Write-Host 'Enter the existing SSH password locally. This stages private configuration only.'
$ErrorActionPreference='Continue'
($remote.Replace("`r`n","`n") + "`n") | ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -T "root@$ServerHost" 'bash -s 2>&1' | Tee-Object -FilePath $log
$result=$LASTEXITCODE
$ErrorActionPreference='Stop'
if ($result -ne 0) { throw "Staging stopped. Inspect $log; the running deployment was not reconfigured." }
Write-Host "Staging log: $log"
