#!/usr/bin/env bash
# NightWatch agent installer: puts the agent into a virtualenv and runs it as a systemd service.
#
#   curl -fsSL https://nightwatch.example.com/agent/install.sh \
#     | sudo bash -s -- --url https://nightwatch.example.com --token nw_xxx
#
# From a repository checkout:  sudo ./agent/install.sh --url ... --token ...
# Re-running upgrades the agent; an existing config is kept unless --force-config is given.
set -euo pipefail

INSTALL_DIR=/opt/nightwatch-agent
CONFIG_DIR=/etc/nightwatch
CONFIG_FILE="$CONFIG_DIR/agent.yaml"
UNIT_FILE=/etc/systemd/system/nightwatch-agent.service
SERVICE_USER=nightwatch

URL=""
TOKEN=""
INTERVAL=15
SERVICES=""
FORCE_CONFIG=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: install.sh --url URL --token TOKEN [options]

  --url URL            NightWatch backend base URL, e.g. https://nightwatch.example.com
  --token TOKEN        server token from the NightWatch web UI
  --interval SECONDS   report interval (default: $INTERVAL)
  --services LIST      comma-separated systemd units to watch (default: auto-detect)
  --force-config       overwrite an existing $CONFIG_FILE
  -h, --help           show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url) URL="${2:-}"; shift 2 ;;
        --token) TOKEN="${2:-}"; shift 2 ;;
        --interval) INTERVAL="${2:-}"; shift 2 ;;
        --services) SERVICES="${2:-}"; shift 2 ;;
        --force-config) FORCE_CONFIG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run as root (sudo)."
[[ -n "$URL" && -n "$TOKEN" ]] || { usage >&2; die "--url and --token are required."; }
[[ "$URL" =~ ^https?:// ]] || die "--url must start with http:// or https://"
[[ "$INTERVAL" =~ ^[0-9]+$ && "$INTERVAL" -ge 5 ]] || die "--interval must be a number >= 5."
URL="${URL%/}"

command -v systemctl >/dev/null || die "systemd is required."
command -v python3 >/dev/null || die "python3 is required (apt install python3 python3-venv)."
python3 - <<'PY' || die "Python 3.9+ is required."
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
python3 -c 'import venv, ensurepip' 2>/dev/null \
    || die "python3-venv is missing (apt install python3-venv)."

# --- Agent files: from this checkout if present, otherwise from the backend ---------------
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

fetch() {  # fetch <file name> <repo-relative fallback path>
    local name="$1" local_path="$2"
    if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/$local_path" ]]; then
        cp "$SCRIPT_DIR/$local_path" "$WORK_DIR/$name"
    else
        command -v curl >/dev/null || die "curl is required."
        curl -fsSL --retry 3 "$URL/agent/$name" -o "$WORK_DIR/$name" \
            || die "cannot download $URL/agent/$name"
    fi
}

info "Fetching agent files"
fetch nightwatch_agent.py nightwatch_agent.py
fetch requirements.txt requirements.txt
fetch nightwatch-agent.service ../deploy/nightwatch-agent.service

# --- System user ---------------------------------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    info "Creating system user $SERVICE_USER"
    useradd --system --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# Read access to journald and /var/log (group names differ between distributions).
for group in systemd-journal adm; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" "$SERVICE_USER"
    fi
done

# --- Code + virtualenv ---------------------------------------------------------------------------
info "Installing agent to $INSTALL_DIR"
install -d -m 755 "$INSTALL_DIR"
install -m 755 "$WORK_DIR/nightwatch_agent.py" "$INSTALL_DIR/nightwatch_agent.py"
install -m 644 "$WORK_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# --- Config ----------------------------------------------------------------------------------------
detect_services() {
    local found=() unit
    for unit in ssh sshd nginx apache2 httpd docker postgresql mysql mariadb redis-server redis         cron crond fail2ban; do
        [[ "$(systemctl show -p LoadState --value "$unit.service" 2>/dev/null)" == loaded ]]             || continue
        # Skip aliases (e.g. sshd -> ssh on Debian): they resolve to another unit Id.
        [[ "$(systemctl show -p Id --value "$unit.service" 2>/dev/null)" == "$unit.service" ]]             || continue
        found+=("$unit")
    done
    (IFS=,; echo "${found[*]}")
}

if [[ -f "$CONFIG_FILE" && $FORCE_CONFIG -eq 0 ]]; then
    warn "$CONFIG_FILE exists — keeping it (use --force-config to overwrite)."
else
    [[ -n "$SERVICES" ]] || SERVICES="$(detect_services)"
    info "Writing $CONFIG_FILE (services: ${SERVICES:-none})"
    install -d -m 755 "$CONFIG_DIR"
    {
        echo "# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "api_url: \"$URL\""
        echo "token: \"$TOKEN\""
        echo "interval: $INTERVAL"
        echo "services:"
        IFS=, read -ra units <<<"$SERVICES"
        for unit in "${units[@]}"; do
            [[ -n "$unit" ]] && echo "  - $unit"
        done
        [[ ${#units[@]} -eq 0 ]] && echo "  []"
        echo "journal:"
        echo "  enabled: true"
        echo "  units: []"
        echo "  priority: warning"
        echo "logs:"
        echo "  files: []"
        echo "state_file: /var/lib/nightwatch-agent/state.json"
    } >"$CONFIG_FILE.tmp"
    mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
fi
# The token is a credential: readable by root and the service user only.
chown "root:$SERVICE_USER" "$CONFIG_FILE"
chmod 640 "$CONFIG_FILE"
# State dir (systemd's StateDirectory= manages it too; created now for the test run).
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 /var/lib/nightwatch-agent

# --- systemd ---------------------------------------------------------------------------------------
info "Installing systemd unit"
install -m 644 "$WORK_DIR/nightwatch-agent.service" "$UNIT_FILE"
systemctl daemon-reload

info "Sending a test report"
if runuser -u "$SERVICE_USER" -- env -C / "$INSTALL_DIR/venv/bin/python" \
    "$INSTALL_DIR/nightwatch_agent.py" --config "$CONFIG_FILE" --once 2>/dev/null; then
    info "Backend accepted the test report."
else
    warn "Test report failed — the service will keep retrying. Check: journalctl -u nightwatch-agent"
fi

systemctl enable --now nightwatch-agent.service >/dev/null
systemctl restart nightwatch-agent.service
info "Done. Status: systemctl status nightwatch-agent | Logs: journalctl -u nightwatch-agent -f"
