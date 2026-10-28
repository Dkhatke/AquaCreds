#!/usr/bin/env bash
# Manage the unattended ML pipeline runner as a per-user launchd agent (macOS).
#
#   aquacreds-ml.sh install <repo> [stages…]   install + start (default stages: features_extract train_classifier)
#   aquacreds-ml.sh status                     state, failures, last log lines
#   aquacreds-ml.sh logs                       follow the run log
#   aquacreds-ml.sh start | stop               run now (load if needed) / stop and unload
#   aquacreds-ml.sh reset                      clear a finished/given-up state so the next run starts again
#   aquacreds-ml.sh uninstall                  remove the agent (keeps logs and caches)
set -euo pipefail

LABEL="in.aquacreds.ml-pipeline"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE="$HOME/Library/Application Support/AquaCreds/ml-pipeline"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN="gui/$(id -u)"

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

case "${1:-}" in
  install)
    repo="$(cd "${2:?usage: install <repo> [stages…]}" && pwd)"
    shift 2
    stages="${*:-features_extract train_classifier}"
    [[ -f "$repo/dvc.yaml" ]] || { echo "not an AquaCreds checkout: $repo" >&2; exit 1; }
    case "$repo" in /private/tmp/*|/tmp/*) echo "refusing a /tmp checkout (macOS deletes it)" >&2; exit 1;; esac
    mkdir -p "$STATE" "$(dirname "$PLIST")"
    # Copy the runner out of the checkout so a branch switch or deleted clone can't break the agent.
    install -m 0755 "$HERE/run.sh" "$STATE/run.sh"
    cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$(xml "$STATE/run.sh")</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AQUA_ML_REPO</key><string>$(xml "$repo")</string>
    <key>AQUA_ML_STAGES</key><string>$(xml "$stages")</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>WorkingDirectory</key><string>$(xml "$repo")</string>
  <key>RunAtLoad</key><true/>
  <!-- Restart only after a failure (exit != 0); a finished run stays finished. -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>600</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$(xml "$STATE/launchd.out.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$STATE/launchd.err.log")</string>
</dict>
</plist>
EOF
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $LABEL for $repo (stages: $stages)"
    echo "status: $0 status    logs: $0 logs"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E '^\s+(state|pid|last exit code|runs) ' || echo "agent not loaded"
    cat "$STATE/status.json" 2>/dev/null || echo "no status yet"
    tail -n 5 "$STATE/run.log" 2>/dev/null || true
    ;;
  logs) tail -n 50 -F "$STATE/run.log" ;;
  start)
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || launchctl bootstrap "$DOMAIN" "$PLIST"
    launchctl kickstart "$DOMAIN/$LABEL" ;;
  stop)
    # Unload so launchd doesn't restart it; children of the runner (uv/dvc/python) get TERM too.
    pid="$(cat "$STATE/lock/pid" 2>/dev/null || true)"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    [[ -n "$pid" ]] && pkill -TERM -P "$pid" 2>/dev/null || true
    echo "stopped (run '$0 start' to resume; caches make it continue where it left off)" ;;
  reset) rm -f "$STATE/status.json" "$STATE/failures"; echo "state cleared" ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL (logs kept in $STATE)"
    ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
