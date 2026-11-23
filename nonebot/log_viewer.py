"""Read whitelisted runtime logs for the dashboard.

The dashboard only exposes the files below; there is no arbitrary path
parameter.  Large logs are read from the end so opening the page never loads
tens of megabytes into memory.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NONEBBOT_ROOT = Path(__file__).resolve().parent

MAX_TAIL = 2000
MAX_BYTES = 512 * 1024

SOURCES = (
    ('nonebot.log', '最新运行日志'),
    ('nonebot.out.log', '标准输出'),
    ('nonebot.err.log', '错误输出'),
)

_DAILY_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}'
    r'(?:\.\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6})?'
    r'\.log$'
)

_MASKS = (
    (re.compile(r'(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+'), r'\1***'),
    (re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}'), 'Bearer ***'),
    (re.compile(r'(?i)(cookie\s*[:=]\s*)[^\r\n]+'), r'\1***'),
    (re.compile(
        r'(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|key)'
        r'\s*[:=]\s*[^\s,;]+'
    ), r'\1=***'),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{8,}\b'), 'sk-***'),
)


def mask_secrets(line: str) -> str:
    """Mask common credential shapes before a log line leaves the server."""
    for pattern, replacement in _MASKS:
        line = pattern.sub(replacement, line)
    return line


def _tail(path: Path, limit: int, max_bytes: int = MAX_BYTES) -> tuple[list[str], bool, int]:
    size = path.stat().st_size
    read_size = min(size, max_bytes)
    with path.open('rb') as handle:
        handle.seek(size - read_size)
        raw = handle.read(read_size)
    text = raw.decode('utf-8', errors='replace')
    truncated = size > max_bytes
    if truncated:
        cut = text.find('\n')
        text = text[cut + 1:] if cut >= 0 else ''
    lines = text.splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
        truncated = True
    return lines, truncated, size


def _daily_log(nonebot_root: Path) -> Path | None:
    directory = nonebot_root / 'logs'
    try:
        candidates = [
            path for path in directory.iterdir()
            if path.is_file() and _DAILY_RE.match(path.name)
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def _resolve(name: str, project_root: Path, nonebot_root: Path) -> tuple[str, Path | None]:
    labels = dict(SOURCES)
    if name not in labels:
        raise ValueError('日志名称无效')
    if name == 'nonebot.log':
        return labels[name], _daily_log(nonebot_root)
    base = project_root / 'logs'
    return labels[name], base / name


def _display(path: Path | None, project_root: Path) -> str:
    if path is None:
        return ''
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.name


def sources(*, project_root=None, nonebot_root=None) -> list[dict]:
    project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
    nonebot_root = Path(nonebot_root) if nonebot_root is not None else NONEBBOT_ROOT
    result = []
    for name, label in SOURCES:
        _, path = _resolve(name, project_root, nonebot_root)
        result.append({
            'name': name,
            'label': label,
            'available': bool(path and path.is_file()),
            'path': _display(path, project_root),
        })
    return result


def read_log(name: str, tail: int = 200, *, project_root=None, nonebot_root=None) -> dict:
    project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
    nonebot_root = Path(nonebot_root) if nonebot_root is not None else NONEBBOT_ROOT
    label, path = _resolve(name, project_root, nonebot_root)
    result = {
        'name': name,
        'label': label,
        'path': _display(path, project_root),
        'available': False,
        'lines': [],
        'size': 0,
        'modified': '',
        'truncated': False,
        'sources': sources(project_root=project_root, nonebot_root=nonebot_root),
    }
    if path is None:
        return result
    try:
        resolved = path.resolve()
        allowed = (project_root / 'logs').resolve() if name != 'nonebot.log' else (nonebot_root / 'logs').resolve()
        if resolved.parent != allowed or not resolved.is_file():
            return result
        lines, truncated, size = _tail(resolved, max(1, min(int(tail), MAX_TAIL)))
        stat = resolved.stat()
    except (OSError, ValueError, RuntimeError):
        return result
    result.update({
        'available': True,
        'lines': [mask_secrets(line) for line in lines],
        'size': size,
        'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
        'truncated': truncated,
    })
    return result
