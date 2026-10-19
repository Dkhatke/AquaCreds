#!/usr/bin/env bash
# One-time preparation of a fresh Oracle Cloud Ampere A1 VM (Ubuntu 24.04, ARM64) for AquaCreds.
#
#   scp infra/scripts/bootstrap-vm.sh ubuntu@<vm-ip>:
#   ssh ubuntu@<vm-ip> 'sudo DEPLOY_USER=deploy bash bootstrap-vm.sh'
#
# Idempotent: safe to re-run. It does:
#   1. apt upgrade + base packages        5. a non-root `deploy` user (docker group, your SSH key)
#   2. host firewall: open 80/443         6. SSH hardening (keys only, no root login) + fail2ban
#   3. a 4 GB swap file + VM sysctls      7. unattended security upgrades
#   4. Docker Engine + compose plugin     8. /opt/aquacreds directory layout
# It does NOT touch the OCI Security List (cloud firewall): that is a manual console step, printed at
# the end and described in docs/DEPLOYMENT.md. Both layers must allow 80/443.
#
# Environment: DEPLOY_USER (deploy), APP_DIR (/opt/aquacreds), SWAP_SIZE_GB (4),
#              DEPLOY_SSH_PUBKEY (default: copy the invoking user's ~/.ssh/authorized_keys),
#              AUTO_REBOOT (false; "true" lets unattended-upgrades reboot at 04:30 UTC for kernel fixes)
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${APP_DIR:-/opt/aquacreds}"
SWAP_SIZE_GB="${SWAP_SIZE_GB:-4}"
AUTO_REBOOT="${AUTO_REBOOT:-false}"
export DEBIAN_FRONTEND=noninteractive

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo bash $0)"
# shellcheck source=/dev/null
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "expected Ubuntu, found ${ID:-unknown}"
[[ "${VERSION_ID:-}" == "24.04" ]] || echo "WARNING: tested on Ubuntu 24.04, found ${VERSION_ID:-unknown}"
[[ "$(uname -m)" == "aarch64" ]] || echo "WARNING: expected aarch64 (Ampere A1), found $(uname -m)"

# ----------------------------------------------------------------------------- 1. packages
log "1/8 apt upgrade + base packages"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
apt-get install -y -q ca-certificates curl gnupg git jq unattended-upgrades \
  iptables-persistent netfilter-persistent fail2ban

# ----------------------------------------------------------------------------- 2. host firewall
# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH. Insert ACCEPT rules
# for HTTP/HTTPS (and HTTP/3 over UDP) *before* that REJECT. Done before Docker is installed, and
# persisted by editing rules.v4 directly, so Docker's own chains are never saved into the file.
log "2/8 host firewall (iptables): allow 80/tcp, 443/tcp, 443/udp"
open_port() {
  local proto="$1" port="$2" pos
  if iptables -C INPUT -p "$proto" -m state --state NEW -m "$proto" --dport "$port" -j ACCEPT 2>/dev/null; then
    return 0
  fi
  pos="$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')"
  if [[ -n "$pos" ]]; then
    iptables -I INPUT "$pos" -p "$proto" -m state --state NEW -m "$proto" --dport "$port" -j ACCEPT
  else
    iptables -A INPUT -p "$proto" -m state --state NEW -m "$proto" --dport "$port" -j ACCEPT
  fi
}
open_port tcp 80
open_port tcp 443
open_port udp 443

RULES=/etc/iptables/rules.v4
if [[ ! -s "$RULES" ]]; then
  if command -v docker >/dev/null 2>&1; then
    echo "WARNING: $RULES missing and Docker is installed; not saving (would capture Docker's chains)."
  else
    mkdir -p /etc/iptables
    iptables-save > "$RULES"
  fi
elif ! grep -q -- '--dport 443 -j ACCEPT' "$RULES"; then
  backup="$RULES.bak.$(date +%s)"
  cp "$RULES" "$backup"
  # Insert before the first INPUT REJECT; if there is none, before the *filter table's COMMIT.
  awk '
    function accept() {
      print "-A INPUT -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT"
      print "-A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT"
      print "-A INPUT -p udp -m state --state NEW -m udp --dport 443 -j ACCEPT"
      done = 1
    }
    /^\*/ { table = $0 }
    !done && /^-A INPUT .*-j REJECT/ { accept() }
    !done && table == "*filter" && /^COMMIT/ { accept() }
    { print }
  ' "$backup" > "$RULES.new"
  iptables-restore --test < "$RULES.new" || die "generated $RULES.new is invalid; $RULES unchanged"
  mv "$RULES.new" "$RULES"
fi
echo "Do NOT enable ufw on Oracle images: it conflicts with the shipped iptables rules."

# ----------------------------------------------------------------------------- 3. swap + sysctl
log "3/8 ${SWAP_SIZE_GB} GB swap file + sysctls"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  [[ -f /swapfile ]] || fallocate -l "${SWAP_SIZE_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
cat > /etc/sysctl.d/99-aquacreds.conf <<'EOF'
# Prefer RAM; swap is a safety net against OOM kills, not working memory.
vm.swappiness = 10
# Recommended by Valkey/Redis so background saves (fork) don't fail under memory pressure.
vm.overcommit_memory = 1
EOF
sysctl --system >/dev/null

# ----------------------------------------------------------------------------- 4. docker
log "4/8 Docker Engine + compose plugin (official apt repository)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true
}
EOF
  systemctl restart docker
fi
systemctl enable --now docker >/dev/null
docker compose version

# ----------------------------------------------------------------------------- 5. deploy user
log "5/8 deploy user '$DEPLOY_USER'"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
# NOTE: the docker group is root-equivalent. The deploy user has no sudo; keep its key safe.
usermod -aG docker "$DEPLOY_USER"
deploy_home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$deploy_home/.ssh"
auth="$deploy_home/.ssh/authorized_keys"
if [[ -n "${DEPLOY_SSH_PUBKEY:-}" ]]; then
  grep -qxF "$DEPLOY_SSH_PUBKEY" "$auth" 2>/dev/null || echo "$DEPLOY_SSH_PUBKEY" >> "$auth"
elif [[ ! -s "$auth" && -n "${SUDO_USER:-}" ]]; then
  src="$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys"
  [[ -s "$src" ]] && cp "$src" "$auth"
fi
[[ -f "$auth" ]] && chown "$DEPLOY_USER:$DEPLOY_USER" "$auth" && chmod 600 "$auth"
[[ -s "$auth" ]] || echo "WARNING: $auth is empty; add a public key before logging in as $DEPLOY_USER"

# ----------------------------------------------------------------------------- 6. ssh hardening
log "6/8 SSH: keys only, no root login; fail2ban for sshd"
cat > /etc/ssh/sshd_config.d/60-aquacreds.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
EOF
sshd -t
systemctl reload ssh
systemctl enable --now fail2ban >/dev/null

# ----------------------------------------------------------------------------- 7. auto updates
log "7/8 unattended security upgrades (auto-reboot: $AUTO_REBOOT)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52aquacreds-unattended <<EOF
Unattended-Upgrade::Automatic-Reboot "$AUTO_REBOOT";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
systemctl enable --now unattended-upgrades >/dev/null

# ----------------------------------------------------------------------------- 8. app layout
log "8/8 $APP_DIR layout"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR" "$APP_DIR/.deploy"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR/backups"

cat <<EOF

Bootstrap complete.
  docker:    $(docker --version)
  swap:      $(swapon --show=NAME,SIZE --noheadings | tr '\n' ' ')
  firewall:  iptables INPUT allows 80/tcp, 443/tcp, 443/udp (persisted in $RULES)

MANUAL STEP (OCI console, cannot be scripted from here):
  Networking -> Virtual Cloud Networks -> <your VCN> -> Security Lists -> Default Security List
  -> Add Ingress Rules: source 0.0.0.0/0, TCP, destination port 80   (ACME HTTP-01 + redirect)
                        source 0.0.0.0/0, TCP, destination port 443  (HTTPS)
                        source 0.0.0.0/0, UDP, destination port 443  (HTTP/3, optional)
Next: log in as '$DEPLOY_USER', create $APP_DIR/.env.prod (chmod 600), then run the first deploy.
See docs/DEPLOYMENT.md.
EOF
