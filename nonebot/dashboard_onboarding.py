"""First-run dashboard onboarding state.

The state file lives next to the dashboard auth file.  Keeping it there means
tests and separate installs never share a flag, and a normal ``git pull``
cannot overwrite it because ``nonebot/data`` is ignored.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FILENAME = 'dashboard-onboarding.json'
DEFAULT = {'basics_pending': False}


def path_for(auth_file) -> Path:
    return Path(auth_file).parent / FILENAME


def load(path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(DEFAULT)
    if not isinstance(raw, dict):
        return dict(DEFAULT)
    return {'basics_pending': raw.get('basics_pending') is True}


def _write(data, path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.onboarding-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def mark_pending(path) -> dict:
    _write({'basics_pending': True}, path)
    return load(path)


def complete(path) -> dict:
    _write({'basics_pending': False}, path)
    return load(path)
