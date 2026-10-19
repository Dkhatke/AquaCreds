#!/usr/bin/env bash
# Shared helpers for the production scripts (sourced, not executed).
#
# Layout on the VM (see docs/DEPLOYMENT.md):
#   /opt/aquacreds/.env.prod                     secrets (chmod 600, never in git)
#   /opt/aquacreds/infra/compose/...             compose.prod.yaml + Caddyfile (synced by deploy.yml)
#   /opt/aquacreds/infra/scripts/...             these scripts
#   /opt/aquacreds/.deploy/                      current/previous tag + deploy history
#   /opt/aquacreds/backups/                      local pg_dump copies
#
# Overridable environment (mainly for the local smoke test):
#   ENV_FILE              default: <root>/.env.prod
#   COMPOSE_FILE          default: <root>/infra/compose/compose.prod.yaml (":"-separated list allowed)
#   COMPOSE_PROJECT_NAME  default: the `name:` in compose.prod.yaml (aquacreds-prod)
# shellcheck shell=bash

AQUA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$AQUA_ROOT/.env.prod}"
export COMPOSE_FILE="${COMPOSE_FILE:-$AQUA_ROOT/infra/compose/compose.prod.yaml}"
# The deployed tag is recorded by deploy.sh; use it so ad-hoc commands (exec, logs, restart) target
# the running release rather than the IMAGE_TAG fallback in the env file.
if [[ -z "${IMAGE_TAG:-}" && -s "${STATE_DIR:-$AQUA_ROOT/.deploy}/current_tag" ]]; then
  IMAGE_TAG="$(cat "${STATE_DIR:-$AQUA_ROOT/.deploy}/current_tag")"
  export IMAGE_TAG
fi

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$0")" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

require_env_file() {
  [[ -f "$ENV_FILE" ]] || die "env file not found: $ENV_FILE (copy .env.prod.example and fill it in)"
  local mode
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
  if [[ "$mode" != "600" && "$mode" != "400" ]]; then
    log "WARNING: $ENV_FILE has mode $mode; run: chmod 600 $ENV_FILE"
  fi
}

# Read KEY from the env file without sourcing it (values may contain shell metacharacters).
# Last assignment wins, surrounding quotes are stripped; falls back to $2.
env_get() {
  local key="$1" default="${2:-}" line value
  line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return 0
  fi
  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
    value="${BASH_REMATCH[1]}"
  fi
  printf '%s' "${value:-$default}"
}

dc() { docker compose --env-file "$ENV_FILE" "$@"; }

project_name() {
  dc config 2>/dev/null | awk '/^name:/ {print $2; exit}'
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# Run rclone in a throwaway container against the backup bucket (R2 by default).
# Credentials are passed by variable NAME (-e VAR) so they never appear in `ps` output.
rclone_backup() {
  local network="${BACKUP_DOCKER_NETWORK:-bridge}"
  RCLONE_CONFIG_BK_TYPE=s3 \
  RCLONE_CONFIG_BK_PROVIDER="$(env_get BACKUP_S3_PROVIDER Cloudflare)" \
  RCLONE_CONFIG_BK_ENDPOINT="$(env_get BACKUP_S3_ENDPOINT_URL)" \
  RCLONE_CONFIG_BK_ACCESS_KEY_ID="$(env_get BACKUP_S3_ACCESS_KEY_ID)" \
  RCLONE_CONFIG_BK_SECRET_ACCESS_KEY="$(env_get BACKUP_S3_SECRET_ACCESS_KEY)" \
  RCLONE_CONFIG_BK_REGION="$(env_get BACKUP_S3_REGION auto)" \
  RCLONE_CONFIG_BK_NO_CHECK_BUCKET=true \
  docker run --rm --network "$network" \
    --user "$(id -u):$(id -g)" \
    -e RCLONE_CONFIG_BK_TYPE -e RCLONE_CONFIG_BK_PROVIDER -e RCLONE_CONFIG_BK_ENDPOINT \
    -e RCLONE_CONFIG_BK_ACCESS_KEY_ID -e RCLONE_CONFIG_BK_SECRET_ACCESS_KEY \
    -e RCLONE_CONFIG_BK_REGION -e RCLONE_CONFIG_BK_NO_CHECK_BUCKET \
    -e RCLONE_CONFIG=/tmp/rclone.conf \
    -v "$BACKUP_DIR:/backups" \
    "${RCLONE_IMAGE:-rclone/rclone:1.71}" "$@"
}
