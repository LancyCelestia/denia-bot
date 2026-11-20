"""Theme preset storage and dashboard APIs.

The active appearance stays client-side in localStorage; this module only
stores named preset snapshots.  Presets are kept in one JSON file so there is
no filename/path handling and no path traversal surface.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

PRESET_FILE = Path('data') / 'theme-presets.json'
MAX_PRESETS = 50
MAX_NAME_LENGTH = 32
MAX_CONFIG_BYTES = 4096
MAX_FILE_BYTES = 256 * 1024

MODES = ('system', 'light', 'dark')
SKINS = (
    'default', 'macos', 'neumorphism', 'ghibli', 'watercolor', 'photo',
    'korean', 'shader', 'holy', 'scandi', 'liquid', 'academia',
)
STYLES = ('comfortable', 'compact', 'large')
BG_KINDS = ('none', 'preset', 'url', 'upload')
BG_PRESETS = ('paper', 'dawn', 'forest', 'night', 'grid')
BG_SCOPES = ('hero', 'full')
SIDEBAR_LAYOUTS = ('docked', 'floating', 'line')
ICON_COLORS = ('colored', 'white', 'black')

_UPLOAD_URL = re.compile(
    r'^/api/appearance/background/[0-9]{10,16}-[a-f0-9]{8}\.(?:png|jpg|gif|webp)$'
)
_BAD_NAME_CHARS = set('/\\:*?"<>|')


class PresetError(Exception):
    """Preset validation/storage error with an HTTP status."""

    status = 400


class PresetNotFound(PresetError):
    status = 404


class PresetConflict(PresetError):
    status = 409


class PresetCorrupt(PresetError):
    status = 500


def default_config() -> dict[str, Any]:
    """Fresh default appearance snapshot."""
    return {
        'mode': 'system',
        'skin': 'default',
        'style': 'comfortable',
        'background': {
            'kind': 'none',
            'preset': 'paper',
            'value': '',
            'scope': 'hero',
        },
        'misc': {
            'radius': None,
            'sidebarLayout': 'docked',
            'iconColor': 'colored',
        },
    }


BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    '暖纸默认': {
        'mode': 'system',
        'skin': 'default',
        'style': 'comfortable',
        'background': {'kind': 'none', 'preset': 'paper', 'value': '', 'scope': 'hero'},
        'misc': {'radius': None, 'sidebarLayout': 'docked', 'iconColor': 'colored'},
    },
    'macOS 毛玻璃': {
        'mode': 'system',
        'skin': 'macos',
        'style': 'comfortable',
        'background': {'kind': 'none', 'preset': 'paper', 'value': '', 'scope': 'hero'},
        'misc': {'radius': None, 'sidebarLayout': 'docked', 'iconColor': 'colored'},
    },
    '吉卜力晨光': {
        'mode': 'system',
        'skin': 'ghibli',
        'style': 'large',
        'background': {'kind': 'preset', 'preset': 'dawn', 'value': '', 'scope': 'hero'},
        'misc': {'radius': None, 'sidebarLayout': 'docked', 'iconColor': 'colored'},
    },
    '沉浸摄影夜航': {
        'mode': 'dark',
        'skin': 'photo',
        'style': 'comfortable',
        'background': {'kind': 'preset', 'preset': 'night', 'value': '', 'scope': 'full'},
        'misc': {'radius': 12, 'sidebarLayout': 'floating', 'iconColor': 'colored'},
    },
}


def _choice(value: Any, choices: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise PresetError(f'{field} 无效')
    return value


def normalize_config(raw: Any) -> dict[str, Any]:
    """Validate a preset config and return it with defaults filled in.

    Unknown keys are dropped.  Missing values fall back to ``default_config``
    so presets written by older clients still load.
    """
    if not isinstance(raw, dict):
        raise PresetError('主题配置无效')
    config = default_config()

    for key in ('mode', 'skin', 'style'):
        if key in raw:
            config[key] = _choice(raw[key], {'mode': MODES, 'skin': SKINS, 'style': STYLES}[key], key)

    background = raw.get('background')
    if background is not None:
        if not isinstance(background, dict):
            raise PresetError('背景配置无效')
        kind = _choice(background.get('kind', 'none'), BG_KINDS, '背景类型')
        preset = _choice(background.get('preset', 'paper'), BG_PRESETS, '背景预设')
        scope = _choice(background.get('scope', 'hero'), BG_SCOPES, '背景范围')
        value = background.get('value', '')
        if value is None:
            value = ''
        if not isinstance(value, str):
            raise PresetError('背景值必须是字符串')
        if len(value) > 2048:
            raise PresetError('背景值过长')
        if '"' in value or '\\' in value or '\r' in value or '\n' in value:
            raise PresetError('背景值包含非法字符')
        if kind == 'url':
            try:
                parsed = urlsplit(value)
                hostname = parsed.hostname
                parsed.port
            except ValueError as exc:
                raise PresetError('背景 URL 格式无效') from exc
            if parsed.scheme not in ('http', 'https') or not hostname:
                raise PresetError('背景 URL 必须是 http(s) 地址')
        elif kind == 'upload':
            if not _UPLOAD_URL.fullmatch(value):
                raise PresetError('上传背景必须来自本面板')
        else:
            value = ''
        config['background'] = {
            'kind': kind,
            'preset': preset,
            'value': value,
            'scope': scope,
        }

    misc = raw.get('misc')
    if misc is not None:
        if not isinstance(misc, dict):
            raise PresetError('杂项配置无效')
        radius = misc.get('radius')
        if radius is None or radius == '':
            radius = None
        elif isinstance(radius, bool) or not isinstance(radius, int) or not 0 <= radius <= 24:
            raise PresetError('圆角必须是 0 到 24 的整数')
        layout = _choice(misc.get('sidebarLayout', 'docked'), SIDEBAR_LAYOUTS, '侧边栏布局')
        icon = _choice(misc.get('iconColor', 'colored'), ICON_COLORS, '图标颜色')
        config['misc'] = {
            'radius': radius,
            'sidebarLayout': layout,
            'iconColor': icon,
        }

    size = len(json.dumps(config, ensure_ascii=False).encode('utf-8'))
    if size > MAX_CONFIG_BYTES:
        raise PresetError('主题配置过大')
    return config


def normalize_name(raw: Any) -> str:
    """Validate a user preset name (syntax only; built-in check is separate)."""
    if not isinstance(raw, str):
        raise PresetError('预设名称无效')
    name = raw.strip()
    if not name:
        raise PresetError('预设名称不能为空')
    if len(name) > MAX_NAME_LENGTH:
        raise PresetError(f'预设名称不能超过 {MAX_NAME_LENGTH} 个字符')
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in name):
        raise PresetError('预设名称不能包含控制字符')
    bad = sorted(set(name) & _BAD_NAME_CHARS)
    if bad:
        raise PresetError(f"预设名称不能包含非法字符: {' '.join(bad)}")
    if name.startswith('.') or name.endswith('.'):
        raise PresetError('预设名称不能以点号开头或结尾')
    return name


class ThemePresetStore:
    """Single-file, lock-protected preset store with atomic writes."""

    def __init__(self, root):
        self.path = Path(root) / PRESET_FILE
        self._lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {'version': 1, 'presets': {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return self._empty()
        except OSError as exc:
            raise PresetCorrupt(f'读取主题预设失败: {exc}') from exc
        if size > MAX_FILE_BYTES:
            raise PresetCorrupt(
                f'主题预设文件超过 {MAX_FILE_BYTES // 1024} KB，请修复或删除 data/theme-presets.json'
            )
        try:
            raw = self.path.read_bytes().decode('utf-8')
        except FileNotFoundError:
            return self._empty()
        except UnicodeDecodeError as exc:
            raise PresetCorrupt(
                '主题预设文件不是 UTF-8 编码，请修复或删除 data/theme-presets.json'
            ) from exc
        except OSError as exc:
            raise PresetCorrupt(f'读取主题预设失败: {exc}') from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PresetCorrupt(
                '主题预设文件不是合法 JSON，请修复或删除 data/theme-presets.json'
            ) from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get('presets'), dict)
            or data.get('version') not in (None, 1)
        ):
            raise PresetCorrupt(
                '主题预设文件结构无效，请修复或删除 data/theme-presets.json'
            )
        return {'version': 1, 'presets': dict(data['presets'])}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        if len(payload) > MAX_FILE_BYTES:
            raise PresetConflict('主题预设文件过大，请删除不再使用的预设')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix='theme-presets-', suffix='.tmp', dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass

    def list_presets(self) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """Return (valid presets, all names, invalid user names)."""
        with self._lock:
            data = self._read_unlocked()
        raw_user = data['presets']
        presets: list[dict[str, Any]] = []
        invalid: list[str] = []

        for name, config in BUILTIN_PRESETS.items():
            presets.append({'name': name, 'builtin': True, 'config': normalize_config(config)})

        for name in sorted(raw_user):
            if name in BUILTIN_PRESETS:
                # A manually added user file must never shadow a built-in name.
                continue
            try:
                config = normalize_config(raw_user[name])
            except PresetError:
                invalid.append(name)
                continue
            presets.append({'name': name, 'builtin': False, 'config': config})

        names = list(BUILTIN_PRESETS) + sorted(name for name in raw_user if name not in BUILTIN_PRESETS)
        return presets, names, invalid

    def get(self, raw_name: Any) -> tuple[str, bool, dict[str, Any]]:
        name = normalize_name(raw_name)
        if name in BUILTIN_PRESETS:
            return name, True, normalize_config(BUILTIN_PRESETS[name])
        with self._lock:
            data = self._read_unlocked()
        if name not in data['presets']:
            raise PresetNotFound(f"主题预设 '{name}' 不存在")
        try:
            config = normalize_config(data['presets'][name])
        except PresetError as exc:
            raise PresetCorrupt(f"主题预设 '{name}' 内容无效: {exc}") from exc
        return name, False, config

    def save(self, raw_name: Any, raw_config: Any, overwrite: bool = False) -> tuple[str, bool]:
        name = normalize_name(raw_name)
        if name in BUILTIN_PRESETS:
            raise PresetConflict(f"'{name}' 是内置主题名，请使用其他名称")
        config = normalize_config(raw_config)
        if not isinstance(overwrite, bool):
            raise PresetError('overwrite 必须是布尔值')

        with self._lock:
            data = self._read_unlocked()
            exists = name in data['presets']
            if exists and not overwrite:
                raise PresetConflict(f"主题预设 '{name}' 已存在，如需覆盖请开启覆盖")
            if not exists and len(data['presets']) >= MAX_PRESETS:
                raise PresetConflict(f'主题预设数量已达上限 {MAX_PRESETS}')
            data['presets'][name] = config
            self._write_unlocked(data)
        return name, False

    def delete(self, raw_name: Any) -> tuple[str, bool]:
        name = normalize_name(raw_name)
        if name in BUILTIN_PRESETS:
            raise PresetConflict(f"'{name}' 是内置主题预设，不允许删除")
        with self._lock:
            data = self._read_unlocked()
            if name not in data['presets']:
                raise PresetNotFound(f"主题预设 '{name}' 不存在")
            del data['presets'][name]
            self._write_unlocked(data)
        return name, False


async def _body(req: Request) -> dict[str, Any]:
    try:
        body = await req.json()
    except Exception as exc:  # JSON decode errors vary by Starlette version.
        raise HTTPException(400, '请求格式无效') from exc
    if not isinstance(body, dict):
        raise HTTPException(400, '请求格式无效')
    return body


def register(app, *, authorized, mutate, root):
    store = ThemePresetStore(root)

    @app.get('/api/theme/presets')
    def list_theme_presets(req: Request):
        authorized(req)
        try:
            presets, names, invalid = store.list_presets()
        except PresetError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {
            'presets': presets,
            'names': names,
            'count': len(presets),
            'max': MAX_PRESETS,
            'invalid': invalid,
        }

    @app.post('/api/theme/presets')
    async def save_theme_preset(req: Request):
        mutate(req)
        body = await _body(req)
        try:
            name, builtin = store.save(body.get('name'), body.get('config'), body.get('overwrite', False))
        except PresetError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {'name': name, 'builtin': builtin}

    @app.post('/api/theme/presets/apply')
    async def apply_theme_preset(req: Request):
        mutate(req)
        body = await _body(req)
        try:
            name, builtin, config = store.get(body.get('name'))
        except PresetError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {'name': name, 'builtin': builtin, 'config': config}

    @app.post('/api/theme/presets/delete')
    async def delete_theme_preset(req: Request):
        mutate(req)
        body = await _body(req)
        try:
            name, builtin = store.delete(body.get('name'))
        except PresetError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {'name': name, 'builtin': builtin}
