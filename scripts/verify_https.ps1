param(
    [Parameter(Mandatory=$true)][ValidatePattern('^/etc/shared-booklist/https-staging/[a-f0-9]{32}$')][string]$Stage,
    [switch]$ProductionCertificate,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$output=Join-Path $repoRoot ('.runtime/https_preparation/integration-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
$code64=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $repoRoot 'deploy/verify_https_integration.py')))
$certificateMode=if ($ProductionCertificate) {'--production-certificate'} else {''}
$remote=@"
set -eu
python3 - '$Stage' $certificateMode <<'HTTPS_INTEGRATION_PY'
import base64
exec(compile(base64.b64decode('$code64'), 'verify_https_integration.py', 'exec'))
HTTPS_INTEGRATION_PY
exit 0
"@
$log=Join-Path $output 'server.log'
Write-Host 'Enter the existing SSH password locally. Verification uses a disposable database and publishes no ports.'
$ErrorActionPreference='Continue'
($remote.Replace("`r`n","`n")+"`n") | ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -T "root@$ServerHost" 'bash -s 2>&1' | Tee-Object -FilePath $log
$result=$LASTEXITCODE
$ErrorActionPreference='Stop'
if ($result -ne 0) { throw "Isolated verification stopped. Inspect $log." }
$receiptLine=Get-Content -LiteralPath $log | Where-Object {$_ -like '{"stage":*'} | Select-Object -Last 1
if (-not $receiptLine -or -not ((Get-Content -LiteralPath $log) -contains 'HTTPS_INTEGRATION_SUCCESS')) {
    throw 'Missing successful integration receipt.'
}
$receiptLine | Set-Content -LiteralPath (Join-Path $output 'receipt.json') -Encoding utf8NoBOM
Write-Host "Verified receipt: $output"
