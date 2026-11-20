"""管理面板外观系统的上传背景存储与接口。

背景图只由本面板写入 `data/appearance-backgrounds/`：文件名由服务器生成，
PIL 校验 PNG / JPEG / GIF / WebP（拒绝 SVG），单张 5 MB 上限，最多保留最近
20 张（被 `data/theme-presets.json` 引用的图片不清理）。读取需要登录，文件名
先按白名单正则校验再落盘，因此不接受任意文件名，也不存在路径穿越。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response

import meme_logic

MAX_IMAGE_BYTES = meme_logic.MAX_IMAGE_BYTES
MAX_BACKGROUNDS = 20
DIRECTORY = Path('data') / 'appearance-backgrounds'
PRESET_FILE = Path('data') / 'theme-presets.json'
_NAME = re.compile(r'^[0-9]{10,16}-[a-f0-9]{8}\.(?:png|jpg|gif|webp)$')
_SUFFIX_MIME = {'png': 'image/png', 'jpg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
_MIME_SUFFIX = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}


class BackgroundStore:
    """按白名单命名保存背景图，只保留最新的 `MAX_BACKGROUNDS` 张。"""

    def __init__(self, root):
        self.directory = Path(root) / DIRECTORY
        self.preset_file = Path(root) / PRESET_FILE

    def save(self, content):
        """校验并落盘一张背景图，返回 `(name, mime)`；内容非法时抛 `ValueError`。"""
        if not isinstance(content, bytes) or not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError('图片大小须在 5 MB 以内')
        mime = meme_logic.image_mime(content)
        suffix = _MIME_SUFFIX[mime]
        self.directory.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            name = f'{int(time.time())}-{secrets.token_hex(4)}.{suffix}'
            path = self.directory / name
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(content)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            self._prune()
            return name, mime
        raise OSError('背景图片保存失败')

    def read(self, name):
        """按已校验的文件名读取背景图，返回 `(bytes, mime)`；不存在或名字非法时抛 `ValueError`。"""
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('背景图片不存在')
        path = self.directory / name
        if path.parent != self.directory:
            raise ValueError('背景图片不存在')
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError('背景图片不存在') from exc
        return content, _SUFFIX_MIME[path.suffix[1:]]

    def _preserve(self):
        """收集被主题预设引用的上传文件名；预设文件损坏时返回 `None` 以跳过清理。"""
        try:
            raw = self.preset_file.read_text(encoding='utf-8')
        except FileNotFoundError:
            return set()
        except (OSError, ValueError):
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get('presets'), dict):
            return None
        keep = set()
        for config in data['presets'].values():
            background = config.get('background') if isinstance(config, dict) else None
            if not isinstance(background, dict) or background.get('kind') != 'upload':
                continue
            value = background.get('value')
            if not isinstance(value, str):
                continue
            name = value.rsplit('/', 1)[-1]
            if _NAME.fullmatch(name):
                keep.add(name)
        return keep

    def _prune(self):
        """只保留最新的 `MAX_BACKGROUNDS` 张；被主题预设引用的图片始终保留。"""
        preserved = self._preserve()
        if preserved is None:
            return
        try:
            entries = [path for path in self.directory.iterdir() if _NAME.fullmatch(path.name)]
        except OSError:
            return
        entries.sort(key=_recent_key, reverse=True)
        for path in entries[MAX_BACKGROUNDS:]:
            if path.name in preserved:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def _recent_key(path):
    try:
        return path.stat().st_mtime_ns, path.name
    except OSError:
        return 0, path.name


def register(app, *, authorized, mutate, root):
    store = BackgroundStore(root)

    @app.post('/api/appearance/background')
    async def upload_background(req: Request):
        mutate(req)
        content = bytearray()
        async for chunk in req.stream():
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise HTTPException(413, '图片大小须在 5 MB 以内')
        try:
            name, _ = store.save(bytes(content))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {'url': f'/api/appearance/background/{name}', 'name': name}

    @app.get('/api/appearance/background/{name}')
    def get_background(req: Request, name: str):
        authorized(req)
        try:
            content, mime = store.read(name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(content, media_type=mime, headers={
            'Cache-Control': 'private, max-age=31536000, immutable',
            'X-Content-Type-Options': 'nosniff',
        })
