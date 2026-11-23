#!/usr/bin/env bash
# Show systemd, Docker and HTTP health for the Linux deployment. Read-only.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: bash deploy-linux/status.sh [options]

Options:
  --root PATH       Repository root (default: parent of deploy-linux)
  -h, --help        Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

ROOT="$(CDPATH= cd -- "$ROOT" && pwd)"
DRY_RUN=0
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

printf '== systemd ==\n'
for unit in qqbot qqbot-dashboard qqbot-gsuid-core; do
    if systemctl cat "$unit.service" >/dev/null 2>&1; then
        printf '%-24s %s\n' "$unit" "$(systemctl is-active "$unit" 2>/dev/null || true)"
    else
        printf '%-24s not installed\n' "$unit"
    fi
done

printf '\n== SnowLuma ==\n'
if command -v docker >/dev/null 2>&1 && [ -f "$ROOT/docker/snowluma/.env.local" ] && [ -f "$ROOT/compose.yml" ]; then
    compose_run ps || true
else
    printf 'compose files or docker are not available\n'
fi

printf '\n== HTTP ==\n'
if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 3 http://127.0.0.1:8080/dashboard-health >/dev/null 2>&1; then
        printf 'NoneBot  http://127.0.0.1:8080/dashboard-health  OK\n'
    else
        printf 'NoneBot  http://127.0.0.1:8080/dashboard-health  DOWN\n'
    fi
    if curl -fsS --max-time 3 http://127.0.0.1:8092/ >/dev/null 2>&1; then
        printf 'dashboard http://127.0.0.1:8092/                  OK\n'
    else
        printf 'dashboard http://127.0.0.1:8092/                  DOWN (check DASHBOARD_HOST drop-in)\n'
    fi
else
    printf 'curl is not available\n'
fi

printf '\nlogs: journalctl -u qqbot -n 50; journalctl -u qqbot-dashboard -n 50; docker logs --tail 50 qqbot-snowluma\n'
