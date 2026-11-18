"""Read private runtime values from ignored local files.

Tracked code must not contain account IDs, tokens, or passwords. Values are
resolved in this order:

1. process environment;
2. ``local/bot.env`` (ignored; preferred for this checkout);
3. ``nonebot/.env`` (ignored; standard NoneBot configuration).

Values are never logged or returned in errors. This module intentionally does
not cache, so a corrected ignored file takes effect on the next lookup.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_CANDIDATES = (
    ROOT / "local" / "bot.env",
    Path(__file__).resolve().parent / ".env",
)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_key(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            return _unquote(value)
    return ""


def get_private(key: str, default: str = "") -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    for path in ENV_CANDIDATES:
        value = _read_key(path, key)
        if value:
            return value
    return default
