param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Release,
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_-]*$')][string]$ServerUser='root',
    [ValidateRange(1,65535)][int]$Port=22
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$scriptPath=Join-Path $repoRoot 'deploy/predeploy.sh'
$outputDir=Join-Path $repoRoot '.runtime/server_predeploy'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$outputFile=Join-Path $outputDir ('predeploy-'+$Release+'.log')
# Strict host verification and password entry remain with the existing local SSH client.
$script=([IO.File]::ReadAllText($scriptPath)).Replace("`r`n","`n")
$predeployErrorPreference=$ErrorActionPreference
try {
    # Git and Docker legitimately report progress on stderr. Judge SSH by its exit code.
    $ErrorActionPreference='Continue'
    $script | ssh -p $Port -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -T "$ServerUser@$ServerHost" "bash -s -- $Release" | Tee-Object -FilePath $outputFile
    $predeployExitCode=$LASTEXITCODE
} finally { $ErrorActionPreference=$predeployErrorPreference }
if ($predeployExitCode -ne 0) { throw "Predeployment stopped. Inspect $outputFile; do not restore an old database blindly." }
Write-Host "Predeployment log saved: $outputFile"
