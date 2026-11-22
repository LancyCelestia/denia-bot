#!/usr/bin/env bash
# Shared helpers for deploy-linux/*.sh. ASCII-only so a non-UTF-8 shell can run it.
# The caller must set ROOT (repository root) and may set DRY_RUN=1.

: "${ROOT:?ROOT must be set before sourcing common.sh}"
: "${DRY_RUN:=0}"

log() { printf '[deploy] %s\n' "$*"; }
warn() { printf '[deploy] warning: %s\n' "$*" >&2; }
die() { printf '[deploy] error: %s\n' "$*" >&2; exit 1; }

quote_cmd() {
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
}

run() {
    if [ "$DRY_RUN" = 1 ]; then
        quote_cmd "$@"
    else
        "$@"
    fi
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        run "$@"
    else
        run sudo "$@"
    fi
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

need_file() {
    [ -f "$1" ] || die "missing file: $1"
}

copy_if_missing() {
    local src="$1" dst="$2"
    if [ -e "$dst" ]; then
        log "keep existing $dst"
        return 0
    fi
    need_file "$src"
    log "create $dst"
    run cp -n "$src" "$dst"
}

render_unit() {
    local src="$1" dst="$2" tmp
    need_file "$src"
    tmp="$(mktemp)"
    while IFS= read -r line || [ -n "$line" ]; do
        printf '%s\n' "${line//\/opt\/qqbot/$ROOT}"
    done < "$src" > "$tmp"
    run_root install -m 0644 "$tmp" "$dst"
    rm -f "$tmp"
}

systemctl_run() {
    run_root systemctl "$@"
}

compose_file() {
    if [ -f "$ROOT/compose.yml" ]; then
        printf '%s\n' "$ROOT/compose.yml"
    else
        printf '%s\n' "$ROOT/docker/snowluma/docker-compose.yml"
    fi
}

compose_run() {
    local file
    file="$(compose_file)"
    run docker compose --env-file "$ROOT/docker/snowluma/.env.local" \
        -f "$file" --project-directory "$ROOT" "$@"
}

random_hex() {
    local bytes="${1:-24}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    elif [ -x "$ROOT/.venv/bin/python" ]; then
        "$ROOT/.venv/bin/python" -c "import secrets; print(secrets.token_hex($bytes))"
    else
        die "cannot generate a random secret: install openssl or create $ROOT/.venv first"
    fi
}

replace_line() {
    local file="$1" pattern="$2" replacement="$3"
    if [ "$DRY_RUN" = 1 ]; then
        log "would update $file: $pattern"
        return 0
    fi
    sed -i "s|^$pattern.*|$replacement|" "$file"
}
