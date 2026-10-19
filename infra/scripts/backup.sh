#!/usr/bin/env bash
# Nightly PostgreSQL backup: pg_dump (custom format) -> gzip -> verify -> upload to R2 -> retention.
#
#   infra/scripts/backup.sh            # run by the systemd timer / cron (see infra/scripts/systemd/)
#
# Object name: <BACKUP_PREFIX>/<POSTGRES_DB>-<UTC timestamp>.dump.gz (+ .sha256 alongside).
# Remote retention: objects older than BACKUP_RETENTION_DAYS (default 14) are deleted after a
# successful upload. Local retention: the newest BACKUP_KEEP_LOCAL (default 3) dumps are kept.
# Exits non-zero on ANY failure (so the timer/cron marks the run as failed and alerting can fire).
#
# Required in the env file: BACKUP_S3_ENDPOINT_URL, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY_ID,
# BACKUP_S3_SECRET_ACCESS_KEY.  Optional: BACKUP_S3_PROVIDER (Cloudflare), BACKUP_PREFIX (postgres),
# BACKUP_DIR, BACKUP_KEEP_LOCAL, BACKUP_RETENTION_DAYS, BACKUP_DOCKER_NETWORK (for rclone).
set -euo pipefail

# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_env_file
BACKUP_DIR="$(env_get BACKUP_DIR "$AQUA_ROOT/backups")"
BUCKET="$(env_get BACKUP_S3_BUCKET)"
PREFIX="$(env_get BACKUP_PREFIX postgres)"
RETENTION_DAYS="$(env_get BACKUP_RETENTION_DAYS 14)"
KEEP_LOCAL="$(env_get BACKUP_KEEP_LOCAL 3)"
DB_NAME="$(env_get POSTGRES_DB aquacreds)"

for key in BACKUP_S3_ENDPOINT_URL BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY; do
  [[ -n "$(env_get "$key")" ]] || die "$key is not set in $ENV_FILE"
done
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ && "$RETENTION_DAYS" -ge 1 ]] || die "BACKUP_RETENTION_DAYS must be >= 1"
[[ "$KEEP_LOCAL" =~ ^[0-9]+$ && "$KEEP_LOCAL" -ge 1 ]] || die "BACKUP_KEEP_LOCAL must be >= 1"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
name="${DB_NAME}-$(date -u +%Y%m%dT%H%M%SZ).dump.gz"
file="$BACKUP_DIR/$name"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT

log "dumping database '$DB_NAME'"
# -Z0: let gzip do the compression so the artefact is a plain .gz of a custom-format dump.
# shellcheck disable=SC2016  # the variables expand inside the db container
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=0' \
  | gzip -6 > "$partial"

log "verifying dump (gzip integrity + pg_restore --list)"
gzip -t "$partial"
toc_entries="$(gunzip -c "$partial" | dc exec -T db pg_restore --list | grep -cv '^;' || true)"
[[ "$toc_entries" -gt 0 ]] || die "dump has an empty table of contents"
mv "$partial" "$file"
sha256_of "$file" > "$file.sha256"
log "dump ok: $name ($(du -h "$file" | awk '{print $1}'), $toc_entries TOC entries)"

remote="bk:$BUCKET/$PREFIX"
log "uploading to $BUCKET/$PREFIX/"
rclone_backup copy --s3-no-check-bucket "/backups/$name" "$remote/"
rclone_backup copy --s3-no-check-bucket "/backups/$name.sha256" "$remote/"
rclone_backup lsf "$remote/$name" | grep -qx "$name" || die "uploaded object not found in $remote"
log "upload verified"

log "remote retention: deleting objects older than ${RETENTION_DAYS}d"
rclone_backup delete --min-age "${RETENTION_DAYS}d" --include "${DB_NAME}-*.dump.gz*" "$remote/"

find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.dump.gz" -print | sort -r | tail -n +"$((KEEP_LOCAL + 1))" \
  | while read -r old; do rm -f "$old" "$old.sha256"; done

log "backup complete: $PREFIX/$name"
