#!/usr/bin/env bash
# Start the Linux deployment: SnowLuma (Docker) first, then gsuid, NoneBot,
# and the dashboard. Missing dependencies/configuration are bootstrapped through
# install.sh unless --skip-install is given.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
NO_DOCKER=0
NO_SYSTEMD=0
SKIP_INSTALL=0

usage() {
    cat <<'EOF'
Usage: sudo bash deploy-linux/start.sh [options]

Options:
  --root PATH       Repository root (default: parent of deploy-linux)
  --no-docker       Do not start SnowLuma
  --no-systemd      Do not start systemd units; print manual commands
  --skip-install    Do not run install.sh first
  --dry-run         Print actions without changing the system
  -h, --help        Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --no-docker) NO_DOCKER=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
        --skip-install) SKIP_INSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

ROOT="$(CDPATH= cd -- "$ROOT" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

need_file "$ROOT/nonebot/bot.py"
need_file "$ROOT/requirements.lock"
cd "$ROOT"

if [ "$SKIP_INSTALL" -eq 0 ] && [ "$NO_SYSTEMD" -eq 0 ] && systemctl is-active --quiet qqbot 2>/dev/null; then
    log "qqbot is already active; skipping install.sh (stop it first to update dependencies)"
    SKIP_INSTALL=1
fi

if [ "$SKIP_INSTALL" -eq 0 ]; then
    log "checking installation (install.sh --no-pull)"
    install_args=(--root "$ROOT" --no-pull --yes)
    [ "$NO_DOCKER" -eq 1 ] && install_args+=(--no-docker)
    [ "$NO_SYSTEMD" -eq 1 ] && install_args+=(--no-systemd)
    [ "$DRY_RUN" -eq 1 ] && install_args+=(--dry-run)
    bash "$SCRIPT_DIR/install.sh" "${install_args[@]}"
else
    log "skipping install.sh (--skip-install)"
fi

if [ "$NO_DOCKER" -eq 0 ]; then
    if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$ROOT/docker/snowluma/.env.local" ]; then
        die "missing docker/snowluma/.env.local; run deploy-linux/install.sh first"
    fi
    log "starting SnowLuma (Docker)"
    compose_run up -d
    if [ "$DRY_RUN" -eq 0 ]; then
        local_ok=0
        for _ in $(seq 1 30); do
            if docker inspect -f '{{.State.Running}}' qqbot-snowluma 2>/dev/null | grep -q true; then
                local_ok=1
                break
            fi
            sleep 1
        done
        if [ "$local_ok" -eq 1 ]; then
            log "SnowLuma container is running"
        else
            warn "SnowLuma container is not running; check: docker logs qqbot-snowluma"
        fi
    fi
else
    log "skipping SnowLuma (--no-docker)"
fi

if [ "$NO_SYSTEMD" -eq 0 ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
        systemctl cat qqbot.service >/dev/null 2>&1 || die "qqbot.service is not installed; run deploy-linux/install.sh"
        systemctl cat qqbot-dashboard.service >/dev/null 2>&1 || die "qqbot-dashboard.service is not installed; run deploy-linux/install.sh"
    fi
    if systemctl cat qqbot-gsuid-core.service >/dev/null 2>&1; then
        log "starting gsuid_core"
        systemctl_run start qqbot-gsuid-core
    fi
    log "starting qqbot and dashboard"
    systemctl_run start qqbot qqbot-dashboard
    if [ "$DRY_RUN" -eq 0 ]; then
        systemctl_run --no-pager --full status qqbot qqbot-dashboard | head -40 || true
    fi
else
    log "systemd disabled; manual start commands:"
    log "  cd $ROOT/nonebot && $ROOT/.venv/bin/python bot.py"
    log "  cd $ROOT/nonebot && $ROOT/.venv/bin/uvicorn dashboard:app --host 127.0.0.1 --port 8092"
fi

if [ "$DRY_RUN" -eq 0 ] && [ "$NO_SYSTEMD" -eq 0 ]; then
    ready=0
    for _ in $(seq 1 30); do
        if curl -fsS http://127.0.0.1:8080/dashboard-health >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ "$ready" -eq 1 ]; then
        log "NoneBot health endpoint is ready"
    else
        warn "NoneBot health endpoint is not ready; check: journalctl -u qqbot -n 50"
    fi
fi

log "SnowLuma noVNC: http://<host>:6081"
log "SnowLuma WebUI: http://<host>:5099"
log "dashboard: http://127.0.0.1:8092 (or DASHBOARD_HOST drop-in)"
log "status: bash $ROOT/deploy-linux/status.sh"
log "stop:   sudo bash $ROOT/deploy-linux/stop.sh"
