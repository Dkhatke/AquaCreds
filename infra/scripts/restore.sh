#!/usr/bin/env bash
# Restore a backup made by backup.sh into a FRESH database (the live database is not touched).
#
#   infra/scripts/restore.sh --list                                # list backups in R2
#   infra/scripts/restore.sh aquacreds-20261019T013000Z.dump.gz    # fetch from R2, restore to a new DB
#   infra/scripts/restore.sh /opt/aquacreds/backups/<file>.dump.gz # restore a local file
#   options:  --target-db NAME   name of the new database (default: <POSTGRES_DB>_restore_<timestamp>)
#             --replace          after restoring, swap it in as the live database (stops the app briefly;
#                                the old database is kept, renamed <POSTGRES_DB>_pre_restore_<timestamp>)
#             --yes              skip the interactive confirmation (scripted drills only)
# Accepts gzip'ed custom-format dumps (*.dump.gz) and plain custom-format dumps (*.dump, pre-deploy).
set -euo pipefail

# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

SOURCE="" TARGET_DB="" REPLACE=0 ASSUME_YES=0 LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) LIST=1 ;;
    --target-db) TARGET_DB="${2:?--target-db needs a name}"; shift ;;
    --replace) REPLACE=1 ;;
    --yes) ASSUME_YES=1 ;;
    -h|--help) usage ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$SOURCE" ]] || die "only one backup may be given"; SOURCE="$1" ;;
  esac
  shift
done

require_env_file
BACKUP_DIR="$(env_get BACKUP_DIR "$AQUA_ROOT/backups")"
LIVE_DB="$(env_get POSTGRES_DB aquacreds)"
remote="bk:$(env_get BACKUP_S3_BUCKET)/$(env_get BACKUP_PREFIX postgres)"
mkdir -p "$BACKUP_DIR"

if ((LIST)); then
  rclone_backup lsl --include '*.dump.gz' "$remote/"
  exit 0
fi
[[ -n "$SOURCE" ]] || usage

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET_DB="${TARGET_DB:-${LIVE_DB}_restore_${stamp}}"
valid_db() { [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; }
valid_db "$TARGET_DB" || die "invalid database name: $TARGET_DB (lowercase letters, digits, _)"
[[ "$TARGET_DB" != "$LIVE_DB" ]] || die "refusing to restore over the live database; use --replace"

# --- locate the backup
if [[ -f "$SOURCE" ]]; then
  file="$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")"
else
  [[ "$SOURCE" =~ ^[A-Za-z0-9_.-]+\.dump(\.gz)?$ ]] || die "not a local file or a backup object name: $SOURCE"
  mkdir -p "$BACKUP_DIR/restore"
  log "downloading $SOURCE from $remote"
  rclone_backup copy "$remote/$SOURCE" /backups/restore/
  rclone_backup copy "$remote/$SOURCE.sha256" /backups/restore/ 2>/dev/null || true
  file="$BACKUP_DIR/restore/$SOURCE"
  [[ -f "$file" ]] || die "backup $SOURCE not found in $remote"
fi
if [[ -f "$file.sha256" ]]; then
  [[ "$(sha256_of "$file")" == "$(tr -d '[:space:]' < "$file.sha256")" ]] || die "checksum mismatch for $file"
  log "checksum OK"
else
  log "WARNING: no .sha256 next to $file; integrity not verified"
fi
case "$file" in
  *.gz) gzip -t "$file"; reader=(gunzip -c "$file") ;;
  *) reader=(cat "$file") ;;
esac

# --- confirm
# shellcheck disable=SC2016  # the variables expand inside the db container
psql_admin() { dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -qtA "$@"' sh "$@"; }
cat >&2 <<EOF

  RESTORE PLAN
    backup        : $file
    into database : $TARGET_DB   (new; must not exist)
    live database : $LIVE_DB     ($([[ $REPLACE -eq 1 ]] && echo "WILL BE REPLACED: app stopped, old DB renamed ${LIVE_DB}_pre_restore_${stamp}" || echo "not touched"))
    compose proj. : $(project_name)

EOF
if ((!ASSUME_YES)); then
  [[ -t 0 ]] || die "no terminal for the confirmation prompt (use --yes for scripted drills)"
  read -r -p "Type the target database name ($TARGET_DB) to continue: " answer
  [[ "$answer" == "$TARGET_DB" ]] || die "confirmation did not match; nothing was changed"
fi

exists="$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'")"
[[ -z "$exists" ]] || die "database $TARGET_DB already exists"

log "creating database $TARGET_DB"
psql_admin -c "CREATE DATABASE \"$TARGET_DB\""
log "restoring (pg_restore --exit-on-error)"
# shellcheck disable=SC2016  # the variables expand inside the db container
if ! "${reader[@]}" | dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --exit-on-error' sh "$TARGET_DB"; then
  die "pg_restore failed; the partial database $TARGET_DB was left for inspection (DROP DATABASE it when done)"
fi

# --- sanity check
# shellcheck disable=SC2016
summary="$(dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -qtA' sh "$TARGET_DB" <<'SQL'
SELECT (SELECT version_num FROM alembic_version LIMIT 1) || ' / '
    || (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') || ' tables';
SQL
)"
log "restored $TARGET_DB: alembic revision / tables = $summary"

if ((!REPLACE)); then
  log "done. The live database is untouched. Inspect with:"
  log "  docker compose --env-file $ENV_FILE exec db psql -U $(env_get POSTGRES_USER aquacreds) -d $TARGET_DB"
  exit 0
fi

# --- swap in as the live database
if ((!ASSUME_YES)); then
  read -r -p "Type 'REPLACE $LIVE_DB' to stop the app and swap databases: " answer
  [[ "$answer" == "REPLACE $LIVE_DB" ]] || die "not confirmed; $TARGET_DB is restored but NOT live"
fi
app_services=(caddy api worker-default worker-geo worker-chain beat)
log "stopping app services: ${app_services[*]}"
dc stop "${app_services[@]}"
old_db="${LIVE_DB}_pre_restore_${stamp}"
psql_admin -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$LIVE_DB', '$TARGET_DB') AND pid <> pg_backend_pid()" >/dev/null
psql_admin -c "ALTER DATABASE \"$LIVE_DB\" RENAME TO \"$old_db\""
psql_admin -c "ALTER DATABASE \"$TARGET_DB\" RENAME TO \"$LIVE_DB\""
log "databases swapped (previous live DB kept as $old_db); starting app services"
dc start "${app_services[@]}"
log "done. Check https://$(env_get API_DOMAIN)/readyz, then DROP DATABASE $old_db once satisfied."
