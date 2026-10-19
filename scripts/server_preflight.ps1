param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost,
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_-]*$')][string]$ServerUser = 'root',
    [ValidateRange(1,65535)][int]$Port = 22,
    [ValidatePattern('^[a-f0-9]{40}$')][string]$Release
)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'server_preflight.py'
$outputDir = Join-Path (Split-Path $PSScriptRoot -Parent) '.runtime/server_predeploy'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
# SSH reads the password from the local console; it is never a script argument or a file.
$inventory = Get-Content -Raw -LiteralPath $scriptPath | ssh -p $Port -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -T "$ServerUser@$ServerHost" "python3 -B - $Release"
if ($LASTEXITCODE -ne 0) { throw 'SSH inventory failed; no deployment performed.' }
$parsed = ($inventory -join "`n") | ConvertFrom-Json
if ($parsed.schema_version -ne 1) { throw 'Unexpected inventory output.' }
$outputFile = Join-Path $outputDir $(if ($Release) {'verification-'+$Release+'.json'} else {'preflight.json'})
$inventory | Set-Content -LiteralPath $outputFile -Encoding utf8
Write-Host "Read-only check saved: $outputFile"
if ($Release -and -not $parsed.deployment_verification.passed) { throw 'Private deployment verification failed; inspect the saved report.' }
