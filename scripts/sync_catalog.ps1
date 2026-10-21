param(
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [ValidatePattern('^[a-f0-9]{40}$')][string]$Release,
    [switch]$WaitForOperator
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$review=Get-Content -Raw -LiteralPath (Join-Path $PackageDirectory 'review.json') | ConvertFrom-Json
$stamp=Split-Path $PackageDirectory -Leaf
if (-not $Release) {
    $Release=git -C $repoRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $Release -notmatch '^[a-f0-9]{40}$') {throw 'Cannot determine committed release'}
}
if ($stamp -notmatch '^\d{8}T\d{6}Z$' -or $review.sha256 -notmatch '^[a-f0-9]{64}$' -or -not $review.success) {throw 'Invalid frozen package review'}
$archive=Join-Path $PackageDirectory 'catalog.zip'
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $review.sha256) {throw 'Package checksum changed'}
$helper=[IO.File]::ReadAllBytes((Join-Path $repoRoot '.runtime/server_catalog_upload/import_catalog.py'))
$helper64=[Convert]::ToBase64String($helper)
$hash=[Security.Cryptography.SHA256]::Create()
try {$helperHash=([BitConverter]::ToString($hash.ComputeHash($helper))).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
function Save-SyncStatus([string]$phase,[string]$status) {
    @{phase=$phase;status=$status;release=$release;sha256=$review.sha256;updated_at=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PackageDirectory 'sync_status.json') -Encoding utf8NoBOM
}
$remote=@'
set -euo pipefail
umask 077
exec 9>/opt/shared-booklist/deploy.lock
flock -w 30 9 || { echo 'Deployment lock busy'; exit 2; }
checkout=$(readlink -f /opt/shared-booklist/current)
[[ "$checkout" == /opt/shared-booklist/releases/__RELEASE__ ]]
[[ $(cat /var/lib/shared-booklist/predeploy/release.txt) == __RELEASE__ ]]
[[ $(git -C "$checkout" remote get-url origin) == https://github.com/SuzukiRenn/shared-booklist.git ]]
python3 - <<'TRANSFER_PY'
import base64,hashlib,json,os,subprocess
from pathlib import Path
base=Path('/var/lib/shared-booklist/predeploy/incoming')
part=base/'catalog-__STAMP__.zip.part'; folder=base/'__STAMP__'
for path in (part,folder,*folder.parents):
    if path.is_symlink():raise SystemExit('Linked transfer path refused')
if not part.is_file() or part.stat().st_size!=__BYTES__ or hashlib.sha256(part.read_bytes()).hexdigest()!='__SHA__':raise SystemExit('Uploaded package mismatch')
folder.mkdir(mode=0o700,exist_ok=True)
archive=folder/'catalog.zip'
if archive.exists():
    if archive.is_symlink() or hashlib.sha256(archive.read_bytes()).hexdigest()!='__SHA__':raise SystemExit('Existing archive differs')
    part.unlink()
else:os.replace(part,archive)
raw=base64.b64decode('__HELPER__',validate=True)
if hashlib.sha256(raw).hexdigest()!='__HELPER_HASH__':raise SystemExit('Import helper transfer mismatch')
target=folder/'import_catalog.py'
if target.is_symlink():raise SystemExit('Linked import helper refused')
if target.exists():
    if target.read_bytes()!=raw:raise SystemExit('Existing helper differs')
else:target.write_bytes(raw)
for path in (folder,archive,target):
    os.chown(path,10001,10001);os.chmod(path,0o700 if path.is_dir() else 0o600)
print('UPLOAD_HASH_VERIFIED',flush=True)
TRANSFER_PY
incoming=/var/lib/shared-booklist/predeploy/incoming/__STAMP__
cd "$checkout"
export BOOKLIST_RELEASE=__RELEASE__
compose=(docker compose -p shared-booklist-predeploy --env-file /etc/shared-booklist/predeploy.env -f deploy/compose.yaml)
run_import=("${compose[@]}" run -T --rm --no-deps -v "$incoming:/transfer:ro" app python /transfer/import_catalog.py --bundle /transfer/catalog.zip --sha256 __SHA__)
recovery_needed=0
restore_services() {
    code=$?
    trap - EXIT
    if [[ "$recovery_needed" == 1 ]]; then
        "${compose[@]}" up -d --wait --wait-timeout 120 app scheduler </dev/null || { echo SERVICE_RECOVERY_FAILED; exit 1; }
        "${compose[@]}" exec -T app python scripts/predeploy_health.py </dev/null || { echo SERVICE_HEALTH_FAILED; exit 1; }
    fi
    exit "$code"
}
trap restore_services EXIT
"${run_import[@]}" </dev/null
recovery_needed=1
"${compose[@]}" stop app scheduler </dev/null
"${run_import[@]}" --apply </dev/null
"${compose[@]}" up -d --wait --wait-timeout 120 app scheduler </dev/null
"${compose[@]}" exec -T app python scripts/predeploy_health.py </dev/null
recovery_needed=0
"${compose[@]}" run -T --rm --no-deps backup python scripts/sqlite_backup.py --source /var/lib/shared-booklist/booklist.sqlite3 --output-dir /backups --verify-restore </dev/null
python3 - <<'RECEIPT_PY'
import json
from pathlib import Path
receipt=json.loads(Path('/var/lib/shared-booklist/predeploy/data/catalog-transfer-__SHA__.json').read_text())
if receipt.get('success') is not True or receipt['sha256']!='__SHA__' or not receipt['server_contributions_and_settings_preserved']:raise SystemExit('Import receipt mismatch')
print('CATALOG_IMPORT_SUCCESS '+json.dumps(receipt))
RECEIPT_PY
exit 0
'@
$remote=$remote.Replace('__RELEASE__',$release).Replace('__STAMP__',$stamp).Replace('__SHA__',$review.sha256).Replace('__BYTES__',[string]$review.bytes).Replace('__HELPER__',$helper64).Replace('__HELPER_HASH__',$helperHash).Replace("`r`n","`n")+"`n"
try {
    Save-SyncStatus 'upload' 'running'
    Write-Host 'Phase 1: enter SSH password for SCP upload. No server data is changed by this phase.'
    & scp -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes $archive ('root@123.56.222.21:/var/lib/shared-booklist/predeploy/incoming/catalog-'+$stamp+'.zip.part')
    if ($LASTEXITCODE -ne 0) {throw 'Upload failed; import not started'}
    Save-SyncStatus 'verify_import' 'running'
    if ($WaitForOperator) { Read-Host 'Upload complete. Press Enter when ready for the import password prompt' | Out-Null }
    Write-Host 'Phase 2: enter SSH password again for hash verification, backed-up source-only import and service checks.'
    $ErrorActionPreference='Continue'
    $remote | ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -T root@123.56.222.21 'bash -s 2>&1' | Tee-Object -FilePath (Join-Path $PackageDirectory 'import_server.log')
    $result=$LASTEXITCODE
    $ErrorActionPreference='Stop'
    if ($result -ne 0) {throw 'Server sync stopped; inspect import_server.log before retrying'}
    $success=Get-Content -LiteralPath (Join-Path $PackageDirectory 'import_server.log') | Where-Object {$_.StartsWith('CATALOG_IMPORT_SUCCESS ')} | Select-Object -Last 1
    if (-not $success) {throw 'Missing successful import receipt'}
    $success.Substring('CATALOG_IMPORT_SUCCESS '.Length) | Set-Content -LiteralPath (Join-Path $PackageDirectory 'import_receipt.json') -Encoding utf8NoBOM
    Save-SyncStatus 'verified' 'completed'
    Write-Host 'SYNC_COMPLETED: source metadata, covers, preserved server records, backup and health verified.'
} catch {
    Save-SyncStatus 'review_required' 'failed'
    Write-Host ('SYNC_STOPPED: '+$_.Exception.Message) -ForegroundColor Red
    throw
}
