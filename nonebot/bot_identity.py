"""The bot display name shared by the dashboard and every rendered card.

The dashboard writes ``data/bot-name.json`` atomically; the bot process reads
the same file on every card render, so changing the name does not require a
restart.  ``BOT_NAME`` in the process environment or an ignored dotenv file is
used only when the JSON file does not exist.

This module deliberately has no NoneBot imports: the dashboard process has no
driver, and both processes must resolve the name identically.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from private_env import get_private

DEFAULT_NAME = '达妮娅'
PATH = Path(__file__).resolve().parent / 'data' / 'bot-name.json'
ENV_KEYS = ('BOT_NAME', 'QQBOT_NAME')
MAX_LENGTH = 40


def normalize(value: object) -> str:
    """Return a card-safe display name; reject empty/control-heavy values."""
    if not isinstance(value, str):
        raise ValueError('机器人名称必须是文本')
    name = value.strip()
    if not name:
        raise ValueError('机器人名称不能为空')
    if len(name) > MAX_LENGTH:
        raise ValueError(f'机器人名称最多 {MAX_LENGTH} 个字符')
    if any(not char.isprintable() for char in name):
        raise ValueError('机器人名称不能包含换行或控制字符')
    return name


def _read_file(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError):
        return None
    value = raw.get('name') if isinstance(raw, dict) else raw
    try:
        return normalize(value)
    except ValueError:
        return None


def _read_environment() -> str | None:
    for key in ENV_KEYS:
        value = get_private(key)
        if not value:
            continue
        try:
            return normalize(value)
        except ValueError:
            continue
    return None


def resolve(path: Path | str | None = None) -> tuple[str, str]:
    """Return ``(name, source)`` with source ``file``, ``env`` or ``default``."""
    target = Path(path) if path is not None else PATH
    name = _read_file(target)
    if name is not None:
        return name, 'file'
    name = _read_environment()
    if name is not None:
        return name, 'env'
    return DEFAULT_NAME, 'default'


def display_name(path: Path | str | None = None) -> str:
    return resolve(path)[0]


def public(path: Path | str | None = None) -> dict[str, str]:
    name, source = resolve(path)
    return {'name': name, 'source': source, 'default': DEFAULT_NAME}


def save(name: object, path: Path | str | None = None) -> dict[str, str]:
    """Validate and atomically persist the display name."""
    target = Path(path) if path is not None else PATH
    value = normalize(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump({'name': value}, handle, ensure_ascii=False)
    os.replace(temp, target)
    return public(target)


def reset(path: Path | str | None = None) -> dict[str, str]:
    """Delete the override, falling back to env/default."""
    target = Path(path) if path is not None else PATH
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    return public(target)
