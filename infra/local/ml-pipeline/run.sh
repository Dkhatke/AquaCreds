#!/usr/bin/env bash
# Unattended runner for the long DVC pipeline stages (satellite feature extraction → training →
# `dvc push`). It is started by launchd (see aquacreds-ml.sh) and needs no interactive session or
# assistant: it resumes from DVC/per-cell caches, keeps the Mac awake while working, and exits
# non-zero on failure so launchd retries it later.
#
# Exit codes: 0 = finished (or nothing to do / another run active / gave up), 1 = retry later.
set -uo pipefail

REPO="${AQUA_ML_REPO:?set AQUA_ML_REPO to an AquaCreds checkout}"
STATE="${AQUA_ML_STATE:-$HOME/Library/Application Support/AquaCreds/ml-pipeline}"
STAGES="${AQUA_ML_STAGES:-features_extract train_classifier}"
MAX_FAILURES="${AQUA_ML_MAX_FAILURES:-20}"
LOG="$STATE/run.log"

mkdir -p "$STATE"
cd "$REPO" || exit 1

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" >>"$LOG"; }
status() { # status <state> <message>
  local failures; failures="$(cat "$STATE/failures" 2>/dev/null || echo 0)"
  printf '{"state": "%s", "message": "%s", "stages": "%s", "repo": "%s", "failures": %s, "updated_at": "%s"}\n' \
    "$1" "$2" "$STAGES" "$REPO" "$failures" "$(ts)" >"$STATE/status.json"
}
notify() { osascript -e "display notification \"$1\" with title \"AquaCreds ML pipeline\"" >/dev/null 2>&1 || true; }

# A finished run stays finished until someone resets it (aquacreds-ml.sh reset).
if grep -q '"state": "succeeded"' "$STATE/status.json" 2>/dev/null; then exit 0; fi

# One run at a time (mkdir is atomic). A lock left behind by a dead process is removed.
if ! mkdir "$STATE/lock" 2>/dev/null; then
  holder="$(cat "$STATE/lock/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then exit 0; fi
  rm -rf "$STATE/lock"; mkdir "$STATE/lock" || exit 1
fi
echo $$ >"$STATE/lock/pid"
trap 'rm -rf "$STATE/lock"' EXIT

# Don't race a run someone started by hand in the same checkout (e.g. an agent's `dvc repro`):
# wait for it, then let `dvc repro` pick up its cached results.
foreign_run() {
  local pid cwd
  for pid in $(pgrep -f 'features_extract.py|train_classifier.py|dvc repro' || true); do
    [[ "$pid" == "$$" ]] && continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    [[ "$cwd" == "$REPO"* ]] && return 0
  done
  return 1
}
while foreign_run; do status waiting "another run is active in this checkout"; sleep 300; done

log "start: stages=[$STAGES]"
for stage in $STAGES; do
  status running "dvc repro $stage"
  log "dvc repro $stage"
  if ! caffeinate -i uv run dvc repro "$stage" >>"$LOG" 2>&1; then
    failures=$(( $(cat "$STATE/failures" 2>/dev/null || echo 0) + 1 ))
    echo "$failures" >"$STATE/failures"
    if (( failures >= MAX_FAILURES )); then
      status gave_up "$stage failed $failures times; see run.log"
      notify "Gave up after $failures failures in $stage. See run.log."
      log "gave up"
      exit 0
    fi
    status failed "$stage failed (attempt $failures); launchd will retry"
    log "$stage failed (attempt $failures)"
    exit 1
  fi
done

# Copy outputs to the DVC remote (local stopgap store or R2) when one is configured.
if uv run dvc remote list 2>/dev/null | grep -q .; then
  status running "dvc push"
  if ! uv run dvc push >>"$LOG" 2>&1; then
    status failed "dvc push failed; launchd will retry"; log "dvc push failed"; exit 1
  fi
fi

echo 0 >"$STATE/failures"
status succeeded "all stages done; outputs pushed"
log "succeeded"
notify "Feature extraction + training finished. Results in $REPO"
exit 0
