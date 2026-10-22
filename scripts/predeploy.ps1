param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Release,
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_-]*$')][string]$ServerUser='root',
    [ValidateRange(1,65535)][int]$Port=22,
    [switch]$InstallSourceUpdates,
    [switch]$ArchiveEmptyPreviewTitleStop
)
$ErrorActionPreference='Stop'
if($ArchiveEmptyPreviewTitleStop -and !$InstallSourceUpdates){throw 'Preview recovery requires source setup'}
$repoRoot=Split-Path $PSScriptRoot -Parent
$scriptPath=Join-Path $repoRoot 'deploy/predeploy.sh'
$outputDir=Join-Path $repoRoot '.runtime/server_predeploy'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$outputFile=Join-Path $outputDir ('predeploy-'+$Release+'.log')
# Strict host verification and password entry remain with the existing local SSH client.
$script=([IO.File]::ReadAllText($scriptPath)).Replace("`r`n","`n")
# Bundle only committed Git objects reachable from the requested release, never workspace files.
$bundlePath=Join-Path $outputDir ('release-'+$Release+'-'+[Guid]::NewGuid().ToString('N')+'.bundle')
try {
    $localRelease=git -C $repoRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $localRelease -ne $Release) { throw 'Requested release must equal the local committed HEAD.' }
    git -C $repoRoot bundle create $bundlePath HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the requested committed release.' }
    $bundleHasher=[Security.Cryptography.SHA256]::Create()
    try { $bundleHash=([BitConverter]::ToString($bundleHasher.ComputeHash([IO.File]::ReadAllBytes($bundlePath)))).Replace('-','').ToLowerInvariant() }
    finally { $bundleHasher.Dispose() }
    $bundleBase64=[Convert]::ToBase64String([IO.File]::ReadAllBytes($bundlePath))
} finally {
    if (Test-Path -LiteralPath $bundlePath) { Remove-Item -LiteralPath $bundlePath }
}
$bundleScript=@'
echo 'PHASE receive_committed_release_via_ssh'
python3 - "$checkout/.git/predeploy.bundle" <<'RELEASE_BUNDLE_PY'
import base64, hashlib, os, sys
payload = base64.b64decode('__BUNDLE_BASE64__', validate=True)
if hashlib.sha256(payload).hexdigest() != '__BUNDLE_SHA256__':
    raise SystemExit('Release transfer checksum mismatch')
with os.fdopen(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), 'wb') as output:
    output.write(payload)
print('release_transfer_verified')
RELEASE_BUNDLE_PY
'@
$bundleScript=$bundleScript.Replace('__BUNDLE_BASE64__',$bundleBase64).Replace('__BUNDLE_SHA256__',$bundleHash).Replace("`r`n","`n")
if (-not $script.Contains('# LOCAL_RELEASE_BUNDLE')) { throw 'Deployment script lacks release transfer support.' }
$script=$script.Replace('# LOCAL_RELEASE_BUNDLE',$bundleScript)
$predeployErrorPreference=$ErrorActionPreference
try {
    # Merge remote stderr before SSH so Git/Docker progress is retained in the log.
    # Local SSH diagnostics remain on stderr. Judge SSH by its exit code.
    $ErrorActionPreference='Continue'
    $taskRemoteCommand="bash -s -- $Release 2>&1"
    if($InstallSourceUpdates){
        $taskRemoteCommand+=" && python3 /opt/shared-booklist/releases/$Release/deploy/install_source_updates.py"
        if($ArchiveEmptyPreviewTitleStop){$taskRemoteCommand+=' --archive-empty-preview-title-stop'}
        $taskRemoteCommand+=' 2>&1'
    }
    $script | ssh -p $Port -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -T "$ServerUser@$ServerHost" $taskRemoteCommand | Tee-Object -FilePath $outputFile
    $predeployExitCode=$LASTEXITCODE
} finally { $ErrorActionPreference=$predeployErrorPreference }
if ($predeployExitCode -ne 0) { throw "Predeployment stopped. Inspect $outputFile; do not restore an old database blindly." }
Write-Host "Predeployment log saved: $outputFile"
