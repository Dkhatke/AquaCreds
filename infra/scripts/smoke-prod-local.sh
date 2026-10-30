#!/usr/bin/env bash
# Local smoke test of the PRODUCTION stack (compose.prod.yaml + Caddyfile + deploy/backup/restore
# scripts) on a developer machine. Contacts no cloud provider: S3 is a throwaway SeaweedFS
# (compose.smoke.yaml), TLS is Caddy's internal CA for `localhost`, secrets are random per run.
#
#   infra/scripts/smoke-prod-local.sh            # builds the api/worker/web images, runs, tears down
#   SMOKE_SKIP_BUILD=1 SMOKE_TAG=mytag ...       # reuse images aquacreds-{api,worker,web}:mytag
#
# The web app is served at https://web.localhost:$HTTPS_PORT (the API at https://localhost). Demo mode
# stays OFF here: `aqua-api seed-demo` refuses APP_ENV=production, so the demo cannot be seeded in this
# stack (FEATURE_DEMO / DEMO_MODE_ENABLED are therefore unset). Browser checks need Playwright's
# Chromium (pnpm install + `pnpm exec playwright install chromium`); otherwise they fall back to
# curl + header assertions (SMOKE_REQUIRE_BROWSER=1 makes the fallback an error).
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
  step "build images (api, worker, web) -> aquacreds-{api,worker,web}:$SMOKE_TAG"
  for target in api worker; do
    docker build -q -f "$REPO/infra/docker/python.Dockerfile" --target "$target" \
      -t "aquacreds-$target:$SMOKE_TAG" "$REPO" >/dev/null
  done
  docker build -q -f "$REPO/apps/web/Dockerfile" -t "aquacreds-web:$SMOKE_TAG" "$REPO" >/dev/null
fi

step "generate throwaway secrets in $TMP"
rnd() { openssl rand -hex "$1"; }
s3_key="smoke$(rnd 4)" s3_secret="$(rnd 24)" bk_key="backup$(rnd 4)" bk_secret="$(rnd 24)"
# Throwaway secrets that pass assert_production_safe (a random Fernet key; a fresh BIP-39 mnemonic).
mfa_key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
relayer_mnemonic="$(docker run --rm --entrypoint python "aquacreds-api:$SMOKE_TAG" -c \
  'from eth_account import Account; Account.enable_unaudited_hdwallet_features(); print(Account.create_with_mnemonic()[1])')"
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
WEB_IMAGE=aquacreds-web
API_DOMAIN=localhost
WEB_DOMAIN=web.localhost
ACME_EMAIL=smoke@example.org
EXPOSE_API_DOCS=false
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=$HTTPS_PORT
CORS_ORIGINS=https://web.localhost:$HTTPS_PORT
FEATURE_TILES=1
JWT_SECRET=$(rnd 32)
MFA_ENCRYPTION_KEY=$mfa_key
RELAYER_MNEMONIC=$relayer_mnemonic
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
web="https://web.localhost:$HTTPS_PORT"
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

step "web app through Caddy at $web"
resolve=(--resolve "web.localhost:$HTTPS_PORT:127.0.0.1")
page="$(curl -fsSk "${resolve[@]}" "$web/")"
grep -q 'verifiable carbon evidence' <<<"$page" || fail "landing page did not render"
echo "landing page renders"
loc="$(curl -sk "${resolve[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "$web/console")"
[[ "$loc" == 307\ *"/console/login"* || "$loc" == 30[78]\ *"/console/login"* ]] || fail "/console did not redirect to login: $loc"
echo "/console -> $loc"
whead="$(curl -sSk "${resolve[@]}" -D - -o /dev/null "$web/")"
for h in strict-transport-security content-security-policy x-content-type-options referrer-policy x-frame-options permissions-policy; do
  grep -qi "^$h:" <<<"$whead" || fail "web: missing header $h"
done
grep -qi '^server:\|^x-powered-by:' <<<"$whead" && fail "web: Server/X-Powered-By leaked"
csp="$(grep -i '^content-security-policy:' <<<"$whead" | tr -d '\r')"
for d in "default-src 'self'" "worker-src 'self' blob:" "tiles.openfreemap.org" "frame-ancestors 'none'" "object-src 'none'"; do
  grep -qF "$d" <<<"$csp" || fail "web CSP lacks: $d"
done
grep -q "unsafe-eval" <<<"$csp" && fail "web CSP allows unsafe-eval"
echo "web security headers + CSP present"
code="$(curl -sk "${resolve[@]}" -o /dev/null -w '%{http_code}' "$web/vendor/maplibre-gl-worker.mjs")"
[[ "$code" == "200" ]] || fail "MapLibre worker /vendor/maplibre-gl-worker.mjs returned $code"
echo "MapLibre worker served from /vendor"
# CORS: the API must allow the web origin (map tiles are fetched cross-origin from the API).
acao="$(curl -sSk -D - -o /dev/null -H "Origin: $web" "$base/healthz" | tr -d '\r' | grep -i '^access-control-allow-origin:' || true)"
grep -qF "$web" <<<"$acao" || fail "API CORS does not allow $web (got: ${acao:-none})"
echo "API CORS allows $web"

step "smoke user via CLI + login through the BFF"
smoke_email="smoke-user@example.org"; smoke_pw="$(rnd 12)"
printf '%s\n' "$smoke_pw" | dc exec -T api aqua-api create-user --email "$smoke_email" --name "Smoke User" --role user >/dev/null \
  || fail "aqua-api create-user failed"
jar="$TMP/cookies.txt"
code="$(curl -sk "${resolve[@]}" -c "$jar" -o /dev/null -w '%{http_code}' -X POST "$web/bff/auth/login" \
  -H 'content-type: application/json' -H 'x-aquacreds-csrf: 1' -H "Origin: $web" \
  --data "{\"email\":\"$smoke_email\",\"password\":\"$smoke_pw\"}")"
[[ "$code" == "200" ]] || fail "BFF login returned $code"
grep -q 'aq_at' "$jar" && grep -E 'HttpOnly' "$jar" | grep -q aq_at || fail "aq_at cookie missing or not HttpOnly"
echo "BFF login OK (aq_at httpOnly cookie set)"
code="$(curl -sk "${resolve[@]}" -b "$jar" -o /dev/null -w '%{http_code}' "$web/console")"
[[ "$code" == "200" ]] || fail "/console with session returned $code"
echo "/console with session -> 200"

step "browser: landing page CSP + login (headless Chromium)"
if (cd "$REPO/apps/web" && node -e 'require.resolve("@playwright/test")' 2>/dev/null) \
   && node "$REPO/infra/scripts/smoke-web-browser.mjs" "$web" "$smoke_email" "$smoke_pw"; then
  :
elif [[ "${SMOKE_REQUIRE_BROWSER:-0}" == "1" ]]; then
  fail "browser checks failed or Playwright unavailable"
else
  echo "note: Playwright/Chromium unavailable or failed; header assertions above are the fallback"
fi

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
