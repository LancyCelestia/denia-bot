#!/usr/bin/env bash
# Stop the Linux deployment without deleting data or containers.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
NO_DOCKER=0
NO_SYSTEMD=0

usage() {
    cat <<'EOF'
Usage: sudo bash deploy-linux/stop.sh [options]

Options:
  --root PATH       Repository root (default: parent of deploy-linux)
  --no-docker       Do not stop SnowLuma
  --no-systemd      Do not stop systemd units
  --dry-run         Print actions without changing the system
  -h, --help        Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --no-docker) NO_DOCKER=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

ROOT="$(CDPATH= cd -- "$ROOT" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

if [ "$NO_SYSTEMD" -eq 0 ]; then
    for unit in qqbot-dashboard qqbot qqbot-gsuid-core; do
        if systemctl cat "$unit.service" >/dev/null 2>&1; then
            log "stopping $unit"
            systemctl_run stop "$unit" || true
        fi
    done
else
    log "skipping systemd units (--no-systemd)"
fi

if [ "$NO_DOCKER" -eq 0 ]; then
    if [ -f "$ROOT/docker/snowluma/.env.local" ] && [ -f "$ROOT/compose.yml" ]; then
        log "stopping SnowLuma"
        compose_run stop || true
    else
        log "SnowLuma compose files are not present; skipping"
    fi
else
    log "skipping SnowLuma (--no-docker)"
fi

log "stop complete; data, volumes and containers were preserved"
