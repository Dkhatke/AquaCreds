#!/usr/bin/env bash
# Local smoke test of the PRODUCTION stack (compose.prod.yaml + Caddyfile + deploy/backup/restore
# scripts) on a developer machine. Contacts no cloud provider: S3 is a throwaway SeaweedFS
# (compose.smoke.yaml), TLS is Caddy's internal CA for `localhost`, secrets are random per run.
#
#   infra/scripts/smoke-prod-local.sh            # builds the api/worker images, runs, tears down
#   SMOKE_SKIP_BUILD=1 SMOKE_TAG=mytag ...       # reuse images aquacreds-{api,worker}:mytag
#
# Uses compose project `aquacreds-prodtest` and host ports 18080/18443 so it cannot collide with
# the dev stack. Everything (containers, volumes, temp files) is removed on exit.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_TAG="${SMOKE_TAG:-smoke}"
HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-18443}"
export COMPOSE_PROJECT_NAME="${SMOKE_PROJECT:-aquacreds-prodtest}"
export COMPOSE_FILE="$REPO/infra/compose/compose.prod.yaml:$REPO/infra/compose/compose.smoke.yaml"
export SKIP_PULL=1 READY_TIMEOUT="${READY_TIMEOUT:-240}"

TMP="$(mktemp -d)"
export ENV_FILE="$TMP/.env.prod" STATE_DIR="$TMP/state" SMOKE_S3_CONFIG="$TMP/s3.json"
export BACKUP_DOCKER_NETWORK="${COMPOSE_PROJECT_NAME}_egress"

step() { printf '\n\033[1m### %s\033[0m\n' "$*"; }
fail() { printf '\nSMOKE FAILED: %s\n' "$*" >&2; exit 1; }
cleanup() {
  step "teardown (down -v)"
  docker compose --env-file "$ENV_FILE" down -v --remove-orphans --timeout 20 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

if [[ "${SMOKE_SKIP_BUILD:-0}" != "1" ]]; then
  step "build images (api, worker) -> aquacreds-{api,worker}:$SMOKE_TAG"
  for target in api worker; do
    docker build -q -f "$REPO/infra/docker/python.Dockerfile" --target "$target" \
      -t "aquacreds-$target:$SMOKE_TAG" "$REPO" >/dev/null
  done
fi

step "generate throwaway secrets in $TMP"
rnd() { openssl rand -hex "$1"; }
s3_key="smoke$(rnd 4)" s3_secret="$(rnd 24)" bk_key="backup$(rnd 4)" bk_secret="$(rnd 24)"
cat > "$SMOKE_S3_CONFIG" <<EOF
{"identities": [
  {"name": "app", "credentials": [{"accessKey": "$s3_key", "secretKey": "$s3_secret"}],
   "actions": ["Admin", "Read", "Write", "List", "Tagging"]},
  {"name": "backup", "credentials": [{"accessKey": "$bk_key", "secretKey": "$bk_secret"}],
   "actions": ["Admin", "Read", "Write", "List", "Tagging"]}
]}
EOF
cat > "$ENV_FILE" <<EOF
IMAGE_TAG=$SMOKE_TAG
API_IMAGE=aquacreds-api
WORKER_IMAGE=aquacreds-worker
API_DOMAIN=localhost
ACME_EMAIL=smoke@example.org
EXPOSE_API_DOCS=false
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=$HTTPS_PORT
CORS_ORIGINS=https://app.example.org
JWT_SECRET=$(rnd 32)
POSTGRES_USER=aquacreds
POSTGRES_PASSWORD=$(rnd 24)
POSTGRES_DB=aquacreds
VALKEY_PASSWORD=$(rnd 24)
S3_ENDPOINT_URL=http://s3:8333
S3_PUBLIC_ENDPOINT_URL=https://localhost:$HTTPS_PORT
S3_BUCKET=aquacreds-smoke
S3_ACCESS_KEY_ID=$s3_key
S3_SECRET_ACCESS_KEY=$s3_secret
BACKUP_S3_PROVIDER=Other
BACKUP_S3_ENDPOINT_URL=http://s3:8333
BACKUP_S3_REGION=us-east-1
BACKUP_S3_BUCKET=aquacreds-smoke-backups
BACKUP_S3_ACCESS_KEY_ID=$bk_key
BACKUP_S3_SECRET_ACCESS_KEY=$bk_secret
BACKUP_DIR=$TMP/backups
EOF
chmod 600 "$ENV_FILE"

step "deploy.sh $SMOKE_TAG (first deploy)"
"$REPO/infra/scripts/deploy.sh" "$SMOKE_TAG"

base="https://localhost:$HTTPS_PORT"
step "checks through Caddy at $base"
curl -fsSk "$base/readyz" | tee "$TMP/readyz.json"; echo
grep -q '"status":"ready"' "$TMP/readyz.json" || fail "/readyz not ready"
curl -fsSk "$base/healthz"; echo
for path in /docs /openapi.json /redoc; do
  code="$(curl -sk -o /dev/null -w '%{http_code}' "$base$path")"
  [[ "$code" == "404" ]] || fail "$path returned $code (expected 404)"
  echo "$path -> 404 (blocked)"
done
headers="$(curl -sSk -D - -o /dev/null "$base/healthz")"
for h in strict-transport-security content-security-policy x-content-type-options referrer-policy x-frame-options; do
  grep -qi "^$h:" <<<"$headers" || fail "missing header $h"
done
grep -qi '^server:' <<<"$headers" && fail "Server header leaked"
echo "security headers present; no Server header"
grep -qi '^content-encoding: \(zstd\|gzip\)' < <(curl -sSk -D - -o /dev/null -H 'Accept-Encoding: zstd, gzip' "$base/readyz") \
  && echo "compression OK" || echo "note: /readyz body too small to compress (Caddy minimum length)"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$HTTP_PORT/healthz")"
[[ "$code" == "308" || "$code" == "301" || "$code" == "302" ]] || fail "http -> https redirect returned $code"
echo "http -> https redirect ($code)"
for _ in $(seq 1 10); do
  if curl -fsSk -X POST "$base/api/v1/system/ping-worker"; then echo; ok=1; break; fi
  sleep 3
done
[[ "${ok:-0}" == "1" ]] || fail "API -> queue -> worker round trip failed"

step "container states + memory"
docker ps --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --format '{{.Names}}\t{{.Status}}'
# shellcheck disable=SC2046  # word-splitting the container IDs is intended
docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' \
  $(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")

step "backup.sh -> S3 (SeaweedFS standing in for R2)"
# shellcheck source=infra/scripts/lib.sh
source "$REPO/infra/scripts/lib.sh"
BACKUP_DIR="$TMP/backups"; mkdir -p "$BACKUP_DIR"
rclone_backup mkdir "bk:aquacreds-smoke-backups" >/dev/null
"$REPO/infra/scripts/backup.sh"
"$REPO/infra/scripts/restore.sh" --list
name="$(basename "$(find "$BACKUP_DIR" -maxdepth 1 -name 'aquacreds-*.dump.gz' | sort | tail -n 1)")"
rm -f "$BACKUP_DIR/$name" "$BACKUP_DIR/$name.sha256"   # force the restore to download from S3

step "restore.sh $name -> fresh database"
"$REPO/infra/scripts/restore.sh" "$name" --target-db aquacreds_restore_smoke --yes

step "rollback: deploy a tag whose worker image does not exist"
docker tag "aquacreds-api:$SMOKE_TAG" "aquacreds-api:$SMOKE_TAG-broken"
if "$REPO/infra/scripts/deploy.sh" "$SMOKE_TAG-broken"; then
  docker rmi "aquacreds-api:$SMOKE_TAG-broken" >/dev/null
  fail "deploy of a broken tag reported success"
fi
docker rmi "aquacreds-api:$SMOKE_TAG-broken" >/dev/null
[[ "$(cat "$STATE_DIR/current_tag")" == "$SMOKE_TAG" ]] || fail "current_tag changed after a failed deploy"
curl -fsSk "$base/readyz" >/dev/null || fail "stack not ready after rollback"
echo "rolled back to $SMOKE_TAG and ready"
cat "$STATE_DIR/history.log"

step "SMOKE PASSED"
