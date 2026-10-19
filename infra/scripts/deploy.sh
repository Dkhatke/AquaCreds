#!/usr/bin/env bash
# Deploy (or roll back) the production stack to a given image tag.
#
#   infra/scripts/deploy.sh sha-1a2b3c4     # deploy that tag
#   infra/scripts/deploy.sh --rollback      # redeploy the previously deployed tag
#
# Steps: pull images -> pre-deploy pg_dump -> run `migrate` (one-shot) -> `up -d` -> wait for /readyz
# (inside the API container AND through Caddy) and for every container to be healthy.
# If anything after the migration fails, the previous tag is redeployed and the script exits non-zero.
# A failed pull or migration leaves the running version untouched.
# NOTE: migrations are not reversed by a rollback. Write backwards-compatible (expand/contract)
# migrations; the pre-deploy dump in $BACKUP_DIR is the last resort (see restore.sh).
#
# Environment: ENV_FILE, COMPOSE_FILE, COMPOSE_PROJECT_NAME (see lib.sh), plus
#   READY_TIMEOUT (s, default 300)  SKIP_PULL=1 (use local images)  PREDEPLOY_BACKUP=0 (skip the dump)
set -euo pipefail

# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[[ $# -eq 1 ]] || usage

STATE_DIR="${STATE_DIR:-$AQUA_ROOT/.deploy}"
READY_TIMEOUT="${READY_TIMEOUT:-300}"
mkdir -p "$STATE_DIR"
PREV_TAG="$(cat "$STATE_DIR/current_tag" 2>/dev/null || true)"

case "$1" in
  -h|--help) usage ;;
  --rollback)
    TAG="$(cat "$STATE_DIR/previous_tag" 2>/dev/null || true)"
    [[ -n "$TAG" ]] || die "no previous tag recorded in $STATE_DIR/previous_tag"
    ;;
  *) TAG="$1" ;;
esac
[[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "invalid image tag: '$TAG'"

require_env_file
BACKUP_DIR="$(env_get BACKUP_DIR "$AQUA_ROOT/backups")"

# One deploy at a time (mkdir is atomic and portable; flock is not available on macOS).
LOCK_DIR="$STATE_DIR/lock"
mkdir "$LOCK_DIR" 2>/dev/null || die "another deploy is running (remove $LOCK_DIR if it is stale)"
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

record() { printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >> "$STATE_DIR/history.log"; }

# Readiness through the API container (no DNS/TLS involved) ...
api_ready() {
  dc exec -T api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status == 200 else 1)" \
    >/dev/null 2>&1
}
# ... and through Caddy on this host. `-k` only because the first certificate may still be issuing;
# the request never leaves the machine (--resolve pins the name to 127.0.0.1).
edge_ready() {
  local domain port
  domain="$(env_get API_DOMAIN)"
  port="$(env_get HTTPS_PORT 443)"
  curl -fsS -k --max-time 10 --resolve "$domain:$port:127.0.0.1" "https://$domain:$port/readyz" >/dev/null 2>&1
}
# Every running container of the project is healthy (none starting / unhealthy / restarting).
all_healthy() {
  local project statuses
  project="$(project_name)"
  statuses="$(docker ps -a --filter "label=com.docker.compose.project=$project" \
    --format '{{.Label "com.docker.compose.service"}} {{.State}} {{.Status}}')"
  [[ -n "$statuses" ]] || return 1
  # migrate is a one-shot that has exited 0; ignore it. Anything else must be running and healthy.
  ! grep -v '^migrate exited Exited (0)' <<<"$statuses" | grep -Eqv ' running .*\(healthy\)'
}

wait_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT))
  log "waiting up to ${READY_TIMEOUT}s for /readyz and healthy containers"
  until api_ready && edge_ready && all_healthy; do
    if ((SECONDS >= deadline)); then
      log "not ready in time; container states:"
      docker ps -a --filter "label=com.docker.compose.project=$(project_name)" \
        --format '  {{.Label "com.docker.compose.service"}}: {{.Status}}' >&2 || true
      return 1
    fi
    sleep 5
  done
  log "ready: /readyz OK (container + edge), all containers healthy"
}

start_stack() {
  IMAGE_TAG="$1" dc up -d --remove-orphans
}

rollback() {
  local reason="$1"
  log "deploy of $TAG failed: $reason"
  dc logs --no-color --tail 80 api migrate >&2 || true
  if [[ -z "$PREV_TAG" || "$PREV_TAG" == "$TAG" ]]; then
    record "$TAG" "FAILED(no-rollback-target)"
    die "no previous tag to roll back to"
  fi
  log "rolling back to $PREV_TAG"
  export IMAGE_TAG="$PREV_TAG"
  if start_stack "$PREV_TAG" && wait_ready; then
    record "$TAG" "FAILED(rolled-back-to:$PREV_TAG)"
    die "deploy failed; rolled back to $PREV_TAG"
  fi
  record "$TAG" "FAILED(rollback-failed:$PREV_TAG)"
  die "deploy failed AND rollback to $PREV_TAG did not become ready: investigate now"
}

export IMAGE_TAG="$TAG"
log "deploying tag=$TAG (previous=${PREV_TAG:-none}) project=$(project_name)"
dc config --quiet || die "compose configuration is invalid"

if [[ "${SKIP_PULL:-0}" != "1" ]]; then
  log "pulling images"
  dc pull --quiet || die "image pull failed (is the tag published to GHCR? are you logged in?)"
fi

if [[ "${PREDEPLOY_BACKUP:-1}" == "1" ]] && dc ps --status running --services 2>/dev/null | grep -qx db; then
  mkdir -p "$BACKUP_DIR"
  dump="$BACKUP_DIR/predeploy-$(date -u +%Y%m%dT%H%M%SZ)-${PREV_TAG:-none}.dump"
  log "pre-deploy database dump -> $dump"
  # shellcheck disable=SC2016  # the variables expand inside the db container
  dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$dump.partial" \
    || die "pre-deploy pg_dump failed; aborting before any change"
  mv "$dump.partial" "$dump"
  # Keep the 3 most recent pre-deploy dumps.
  find "$BACKUP_DIR" -maxdepth 1 -name 'predeploy-*.dump' -print | sort -r | tail -n +4 | while read -r old; do rm -f "$old"; done
fi

log "running migrations"
dc up -d --wait db valkey || die "db/valkey did not become healthy"
dc run --rm migrate || die "migration failed; the running version was not changed"

log "starting services"
start_stack "$TAG" || rollback "docker compose up failed"
wait_ready || rollback "services did not become ready within ${READY_TIMEOUT}s"

if [[ "$PREV_TAG" != "$TAG" ]]; then
  printf '%s\n' "${PREV_TAG}" > "$STATE_DIR/previous_tag"
fi
printf '%s\n' "$TAG" > "$STATE_DIR/current_tag"
record "$TAG" "OK"
# Drop dangling layers only; tagged images stay so a rollback needs no download.
docker image prune -f >/dev/null || true
log "deployed $TAG"
