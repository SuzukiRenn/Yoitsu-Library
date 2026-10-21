param(
    [ValidateSet('staging','production')][string]$Environment='staging',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$output=Join-Path $repoRoot ('.runtime/https_preparation/dns-'+$Environment+'-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
# Keep staging accounts and certificates separate from the production CA account.
$directory='/etc/shared-booklist/acme-dns-'+$Environment
$certificate='sharedbooklist-xyz-'+$Environment
$image='certbot/certbot:v5.8.0@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20'
$server=if ($Environment -eq 'staging') {'https://acme-staging-v02.api.letsencrypt.org/directory'} else {'https://acme-v02.api.letsencrypt.org/directory'}
$command=@"
set -eu; umask 077; install -d -m 0700 '$directory'; docker pull --quiet --platform linux/amd64 '$image'; docker run --rm -it --read-only --cap-drop ALL --security-opt no-new-privileges:true --memory 256m --cpus 0.5 --log-driver none --tmpfs /tmp:size=32m,mode=1777 --tmpfs /var/log/letsencrypt:size=16m,mode=1777 --tmpfs /var/lib/letsencrypt:size=16m,mode=1777 -v '$directory`:/etc/letsencrypt' '$image' certonly --manual --preferred-challenges dns --server '$server' --cert-name '$certificate' --key-type ecdsa -d sharedbooklist.xyz; echo DNS_CERTIFICATE_REQUEST_SUCCESS
"@
Write-Host "Certificate environment: $Environment. Website ports and running application remain unchanged."
Write-Host 'Enter SSH password locally, then follow the CA email/terms prompts yourself.'
Write-Host 'At the TXT-record prompt, leave the terminal open. Add the displayed TXT value and wait for DNS verification before pressing Enter.'
Write-Host 'Manual DNS validation does not yet provide automatic renewal.'
$ErrorActionPreference='Continue'
ssh -tt -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes "root@$ServerHost" $command.Replace("`r`n","`n").Trim() | Tee-Object -FilePath (Join-Path $output 'request.log')
$result=$LASTEXITCODE
$ErrorActionPreference='Stop'
if ($result -ne 0) {throw "Certificate request stopped. Inspect $output. No website configuration was activated."}
Write-Host "Certificate request log: $output"
