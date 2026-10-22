#!/usr/bin/env bash
# Explicit commit, separate persistent data, private containers only. No password arguments.
set -euo pipefail
umask 077
release=${1:?full_commit_required}
[[ "$release" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid commit'; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Run as the server operator'; exit 2; }
[[ $(uname -m) == x86_64 ]] || { echo 'Only Linux x86_64 is verified'; exit 2; }
for tool in docker git python3 flock timeout; do command -v "$tool" >/dev/null; done
docker compose version >/dev/null
[[ $(docker info --format '{{.OSType}}') == linux ]] || { echo 'Linux Docker engine required'; exit 2; }
root=/opt/shared-booklist
state=/var/lib/shared-booklist/predeploy
config=/etc/shared-booklist
for path in "$root" "$state" "$state/data" "$state/backups" "$config"; do
  [[ ! -L "$path" ]] || { echo 'Refusing a symlink deployment path'; exit 2; }
done
install -d -m 0700 "$root" "$root/releases" "$config"
exec 9>"$root/deploy.lock"
echo 'PHASE waiting_for_deployment_lock (up to 30 seconds)'
if ! flock -w 30 9; then
  echo 'Another deployment still holds the lock; no new deployment started.'
  if command -v lslocks >/dev/null; then
    lslocks --noheadings --output PID,COMMAND,TYPE,PATH | awk '$NF == "/opt/shared-booklist/deploy.lock"'
  fi
  exit 2
fi
python3 - <<'PY'
import shutil
if shutil.disk_usage('/').free < 3*1024**3: raise SystemExit('Need at least 3 GiB free for build and backup')
PY
checkout="$root/releases/$release"
if [[ ! -d "$checkout" ]]; then
  install -d -m 0700 "$checkout"
  git -C "$checkout" init -q
  git -C "$checkout" remote add origin https://github.com/SuzukiRenn/shared-booklist.git
fi
# LOCAL_RELEASE_BUNDLE
if [[ -f "$checkout/.git/predeploy.bundle" ]]; then
  echo 'PHASE import_fixed_release_from_ssh'
  git -C "$checkout" bundle verify "$checkout/.git/predeploy.bundle"
  git -C "$checkout" fetch "$checkout/.git/predeploy.bundle" HEAD </dev/null
  [[ $(git -C "$checkout" rev-parse FETCH_HEAD) == "$release" ]] || { echo 'Transferred release does not match requested commit'; exit 2; }
  rm -- "$checkout/.git/predeploy.bundle"
else
  echo 'PHASE fetch_fixed_release (up to 120 seconds)'
  GIT_TERMINAL_PROMPT=0 timeout 120 git -c credential.helper= -C "$checkout" fetch --depth 1 origin "$release"
fi
git -C "$checkout" checkout --detach "$release"
[[ $(git -C "$checkout" rev-parse HEAD) == "$release" ]]
git -C "$checkout" diff --exit-code >/dev/null
[[ -z $(git -C "$checkout" status --porcelain --untracked-files=all) ]] || { echo 'Release checkout is modified'; exit 2; }
cd "$checkout"
export BOOKLIST_RELEASE="$release"
export GIT_TERMINAL_PROMPT=0
echo 'PHASE build_private_runtime'
base_image=python:3.14.8-slim-bookworm@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf
if ! docker image inspect "$base_image" >/dev/null 2>&1; then
  timeout 300 docker pull "$base_image"
fi
timeout 7200 docker build --target runtime -t "shared-booklist:$release" -f deploy/Dockerfile .
echo 'PHASE linux_regression_before_live_data'
timeout 3600 docker build --target verification -t "shared-booklist-verify:$release" -f deploy/Dockerfile .
docker run --rm --network none --memory 128m --read-only --tmpfs /tmp:size=32m "shared-booklist-verify:$release" python -c "import json,platform,sqlite3,subprocess;from lxml import etree;print(json.dumps({'verification_python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'lxml':etree.LXML_VERSION,'libxml2':etree.LIBXML_VERSION,'node':subprocess.check_output(['node','--version'],text=True).strip()}))"
docker run --rm --network none --memory 512m --cpus 1 --read-only --tmpfs /tmp:size=128m --tmpfs /app/.runtime:size=16m,uid=10001,gid=10001,mode=0700 "shared-booklist-verify:$release"
docker run --rm --network none --memory 128m --read-only --tmpfs /tmp:size=32m "shared-booklist-verify:$release" python scripts/test_deploy.py
echo 'PHASE initialize_private_configuration'
install -d -m 0700 -o 10001 -g 10001 "$state/data" "$state/backups"
python3 - <<'PY'
import os, secrets, stat
path='/etc/shared-booklist/predeploy.env'
if os.path.lexists(path):
    info=os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != 0:
        raise SystemExit('Existing configuration must be a private root-owned regular file')
else:
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as output:
        output.write('BOOKLIST_ENV=production\nBOOKLIST_SECRET_KEY='+secrets.token_urlsafe(64)+'\nBOOKLIST_ALLOWED_HOSTS=localhost,127.0.0.1\nBOOKLIST_DATA_DIR=/var/lib/shared-booklist\n')
PY
install -d -m 0700 "$config/backups"
install -m 0600 "$config/predeploy.env" "$config/backups/$release-$(date -u +%Y%m%dT%H%M%S)-$$.env"
compose_base=(docker compose -p shared-booklist-predeploy --env-file "$config/predeploy.env" -f deploy/compose.yaml)
compose=("${compose_base[@]}")
services=(app scheduler backup)
if [[ -f "$config/ssh-access/active" ]]; then
  python3 deploy/ssh_access.py prepare "$release"
  compose+=(-f "$config/ssh-access/compose.json")
  services+=(proxy)
fi
"${compose[@]}" config --quiet
# One-off tools must not inherit the running app's fixed edge IP from the
# proxy overlay. They need only the data mount and private base configuration.
"${compose_base[@]}" run -T --rm --no-deps app python scripts/check_production.py </dev/null
echo 'PHASE stop_writes_and_back_up'
trap 'code=$?; "${compose[@]}" stop "${services[@]}" || true; echo "Deployment failed; services stopped and data retained."; exit "$code"' ERR
"${compose[@]}" stop "${services[@]}"
if [[ -f "$state/data/booklist.sqlite3" ]]; then
  "${compose_base[@]}" run -T --rm --no-deps backup python scripts/sqlite_backup.py --source /var/lib/shared-booklist/booklist.sqlite3 --output-dir /backups --verify-restore </dev/null
fi
echo 'PHASE migrate'
# Failure after writes stop leaves the application stopped. No blind rollback across schemas.
"${compose_base[@]}" run -T --rm --no-deps app python manage.py migrate --noinput </dev/null
echo 'PHASE start_and_verify'
if ! "${compose[@]}" up -d --no-build --wait --wait-timeout 120 "${services[@]}"; then
  "${compose[@]}" stop "${services[@]}"
  echo 'Private service check failed. Data retained; operator review required.'
  exit 1
fi
"${compose[@]}" exec -T app python scripts/predeploy_health.py </dev/null
if [[ -f "$config/ssh-access/active" ]]; then
  python3 deploy/ssh_access.py verify "$release"
fi
"${compose_base[@]}" run -T --rm --no-deps backup python scripts/sqlite_backup.py --source /var/lib/shared-booklist/booklist.sqlite3 --output-dir /backups --verify-restore </dev/null
"${compose[@]}" exec -T app python -c "import json,sqlite3,platform;print(json.dumps({'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'ports_published':False,'mode':'private_predeploy'}))" </dev/null
ln -s "$checkout" "$root/current.$$.new"
mv -Tf "$root/current.$$.new" "$root/current"
printf '%s\n' "$release" > "$state/release.txt"
echo "PREDEPLOY_SUCCESS $release"
trap - ERR
# Windows PowerShell may append CRLF after the LF-normalized piped script.
# Exit after the completed deployment, before Bash interprets that trailing CR.
exit 0
