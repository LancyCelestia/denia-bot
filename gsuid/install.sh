#!/usr/bin/env bash
# Install (or refresh) the pinned gsuid_core service and its plugin stack.
#
# Layout produced (all generated at runtime, nothing vendored in this repo):
#   gsuid/core                                   gsuid_core clone + .venv + data
#   gsuid/core/gsuid_core/plugins/XutheringWavesUID
#   gsuid/core/gsuid_core/plugins/EndUID
#   gsuid/core/gsuid_core/plugins/RoverSign
#
# Re-running is safe: existing clones are reused and re-pinned to the exact
# commit recorded in versions.env.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
VERSIONS_FILE="$SCRIPT_DIR/versions.env"
CORE_DIR="$SCRIPT_DIR/core"
PLUGINS_DIR="$CORE_DIR/gsuid_core/plugins"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -f "$VERSIONS_FILE" ] || die "missing $VERSIONS_FILE (expected next to this script)"

# shellcheck source=versions.env
. "$VERSIONS_FILE"

for var in \
  GSUID_CORE_REPO GSUID_CORE_SHA \
  XUTHERINGWAVESUID_REPO XUTHERINGWAVESUID_SHA \
  ENDUID_REPO ENDUID_SHA \
  ROVERSIGN_REPO ROVERSIGN_SHA \
  GENSHINUID_ADAPTER_REQ
do
  [ -n "${!var:-}" ] || die "versions.env does not define $var"
done

for tool in git uv; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    die "'$tool' is required but was not found on PATH"$'\n'"      git: https://git-scm.com/downloads"$'\n'"      uv:  https://docs.astral.sh/uv/getting-started/installation/"
  fi
done

# clone_pin URL DIR SHA
# Reuse DIR when it already is a clone, otherwise shallow-clone URL into it,
# then force HEAD to exactly SHA (fetching that commit when the shallow clone
# does not carry it because the branch head moved after the pin was made).
clone_pin() {
  local url="$1" dir="$2" sha="$3" name head
  name="$(basename -- "$dir")"

  if [ -d "$dir/.git" ]; then
    log "$name: reusing existing clone at $dir"
  else
    if [ -e "$dir" ] && [ -n "$(ls -A -- "$dir" 2>/dev/null || true)" ]; then
      die "$name: $dir exists but is not a git clone; move it away and re-run"
    fi
    mkdir -p -- "$(dirname -- "$dir")"
    log "$name: cloning $url"
    git clone --quiet --depth 1 -- "$url" "$dir" || die "$name: git clone failed ($url)"
  fi

  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" != "$sha" ]; then
    if ! git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null; then
      log "$name: fetching pinned commit $sha"
      git -C "$dir" fetch --quiet --depth 1 origin "$sha" 2>/dev/null || true
      if ! git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null; then
        log "$name: shallow fetch did not yield $sha, fetching full history"
        git -C "$dir" fetch --quiet --unshallow origin 2>/dev/null \
          || git -C "$dir" fetch --quiet origin \
          || die "$name: could not fetch from $url"
      fi
      git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null \
        || die "$name: commit $sha does not exist upstream"
    fi
    git -C "$dir" -c advice.detachedHead=false checkout --quiet --force --detach "$sha" \
      || die "$name: could not check out $sha"
  fi

  head="$(git -C "$dir" rev-parse HEAD)" || die "$name: cannot read HEAD in $dir"
  [ "$head" = "$sha" ] || die "$name: expected commit $sha but HEAD is $head"
  log "$name: pinned at $sha"
}

log "repository root: $REPO_ROOT"

clone_pin "$GSUID_CORE_REPO" "$CORE_DIR" "$GSUID_CORE_SHA"
clone_pin "$XUTHERINGWAVESUID_REPO" "$PLUGINS_DIR/XutheringWavesUID" "$XUTHERINGWAVESUID_SHA"
clone_pin "$ENDUID_REPO" "$PLUGINS_DIR/EndUID" "$ENDUID_SHA"
clone_pin "$ROVERSIGN_REPO" "$PLUGINS_DIR/RoverSign" "$ROVERSIGN_SHA"

log "installing Python 3.13 via uv (no-op when already present)"
uv python install 3.13 || die "uv python install 3.13 failed"

log "syncing gsuid_core environment in $CORE_DIR"
( cd "$CORE_DIR" && uv sync --python 3.13 ) || die "uv sync failed in $CORE_DIR"

log "applying EndUID render-size compatibility patch"
( cd "$CORE_DIR" && uv run python "$SCRIPT_DIR/patches/enduid-render-size.py" "$CORE_DIR" ) \
  || die "EndUID render-size compatibility patch failed"

log "applying command-prefix display patch"
( cd "$CORE_DIR" && uv run python "$SCRIPT_DIR/patches/command-prefix-display.py" "$CORE_DIR" ) \
  || die "command-prefix display patch failed"

log "ensuring pip is importable inside the core venv (required for plugin auto-install)"
( cd "$CORE_DIR" && uv run python -m ensurepip ) \
  || die "python -m ensurepip failed in $CORE_DIR"

log "installing plugin runtime dependencies into the core venv"
( cd "$CORE_DIR" && uv pip install --python .venv/bin/python \
    pypinyin rapidfuzz opencv-python fonttools ) \
  || die "uv pip install of plugin runtime dependencies failed"

cat <<EOF

Installed pinned versions:
  gsuid_core        $GSUID_CORE_SHA
  XutheringWavesUID $XUTHERINGWAVESUID_SHA
  EndUID            $ENDUID_SHA
  RoverSign         $ROVERSIGN_SHA
  NoneBot adapter   $GENSHINUID_ADAPTER_REQ

Next steps:
  1. Start the core:          cd gsuid/core && uv run core
  2. Open http://127.0.0.1:8765/app and set WS_TOKEN plus your master accounts.
  3. Install the adapter into the NoneBot venv ($GENSHINUID_ADAPTER_REQ),
     put the same WS_TOKEN into the NoneBot config, then start NoneBot
     from the repository root.
EOF
