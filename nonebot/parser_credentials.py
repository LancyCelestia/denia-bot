"""Bilibili parser credential import and QR-login helpers for the dashboard.

The dashboard is a separate process from NoneBot. It writes the same
``data/media_parser/config.json`` and ``data/media_parser/cookies`` file that
the parser already owns; the running parser reloads the credential file when
its mtime changes, so a successful login does not need a bot restart.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping

import parser_settings

BILI_KEYS = ('SESSDATA', 'bili_jct', 'DedeUserID')
BILI_REQUIRED = ('SESSDATA', 'bili_jct')
MAX_INPUT = 32768
MAX_VALUE = 4096


def _normalise_name(name: str) -> str | None:
    key = name.strip().lower()
    if key == 'sessdata':
        return 'SESSDATA'
    if key == 'bili_jct':
        return 'bili_jct'
    if key == 'dedeuserid':
        return 'DedeUserID'
    return None


def _validate_value(name: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f'Cookie 字段 {name} 为空')
    if len(value) > MAX_VALUE:
        raise ValueError(f'Cookie 字段 {name} 过长')
    if any(not 0x21 <= ord(ch) <= 0x7e or ch in ';,"\\' for ch in value):
        raise ValueError(f'Cookie 字段 {name} 含非法字符')
    if name == 'DedeUserID' and (not value.isascii() or not value.isdigit() or len(value) > 20):
        raise ValueError('Cookie 字段 DedeUserID 必须是数字')
    return value


def _from_mapping(data: Mapping) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw_name, raw_value in data.items():
        name = _normalise_name(str(raw_name))
        if name is None:
            continue
        cookies[name] = _validate_value(name, str(raw_value))
    return cookies


def _from_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ValueError('JSON 格式无效') from e
    if isinstance(data, dict) and isinstance(data.get('cookies'), (dict, list)):
        data = data['cookies']
    if isinstance(data, dict):
        return _from_mapping(data)
    if isinstance(data, list):
        cookies: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict) or not item.get('name') or not item.get('value'):
                raise ValueError('JSON 数组项须包含 name 和 value')
            name = _normalise_name(str(item['name']))
            if name is not None:
                cookies[name] = _validate_value(name, str(item['value']))
        return cookies
    raise ValueError('JSON 须是对象或 name/value 数组')


def parse_cookie_input(raw: str) -> dict[str, str]:
    """Parse a Cookie header, a JSON object, or a browser name/value array."""
    text = (raw or '').strip()
    if not text:
        raise ValueError('请粘贴 Cookie 或 JSON')
    if len(text) > MAX_INPUT:
        raise ValueError('Cookie / JSON 内容过长')
    if text.lower().startswith('cookie:'):
        text = text[7:].strip()
    if text.startswith('{') or text.startswith('['):
        cookies = _from_json(text)
    else:
        cookies = {}
        for chunk in re.split(r'[;\r\n]+', text):
            chunk = chunk.strip()
            if not chunk:
                continue
            name, sep, value = chunk.partition('=')
            if not sep:
                raise ValueError(f'Cookie 片段缺少 =：{chunk[:40]}')
            key = _normalise_name(name)
            if key is not None:
                cookies[key] = _validate_value(key, value)
    missing = [name for name in BILI_REQUIRED if not cookies.get(name)]
    if missing:
        raise ValueError('缺少 Cookie 字段：' + '、'.join(missing))
    return {name: cookies[name] for name in BILI_KEYS if cookies.get(name)}


def serialize_cookies(cookies: Mapping[str, str]) -> str:
    values = {name: str(cookies.get(name) or '').strip() for name in BILI_KEYS}
    if not values['SESSDATA']:
        raise ValueError('缺少 SESSDATA')
    text = '; '.join(f'{name}={values[name]}' for name in BILI_KEYS if values[name])
    if len(text) > MAX_INPUT:
        raise ValueError('Cookie 内容过长')
    return text


def save_bilibili_config(cookies: Mapping[str, str]) -> dict:
    """Write the Bilibili cookies through the existing parser secret path."""
    text = serialize_cookies(cookies)
    for attempt in range(2):
        data = parser_settings.public()
        if 'bilibili' not in (data.get('platforms') or {}):
            raise ValueError('未找到 B 站解析器配置')
        payload = {
            'version': data['version'],
            'general': {},
            'platforms': {'bilibili': {'cookies': text}},
            'clear_secrets': [],
        }
        try:
            return parser_settings.save(payload)
        except FileExistsError:
            if attempt:
                raise
    raise FileExistsError('配置已被其他操作修改，请刷新后重试')


def write_bilibili_credential(cookies: Mapping[str, str], refresh_token: str = '') -> None:
    """Write the parser's login-state file; the running bot reloads it by mtime."""
    from media_parser.core.config import PluginConfig
    from media_parser.core.parsers.bilibili.login import BilibiliLogin

    BilibiliLogin(PluginConfig()).save_cookies(cookies, refresh_token)


def save_bilibili_cookies(cookies: Mapping[str, str]) -> dict:
    """Paste-import path: save config first, then the login-state file."""
    public = save_bilibili_config(cookies)
    message = 'B 站 Cookie 已导入'
    credential_written = True
    try:
        write_bilibili_credential(cookies)
    except Exception as e:
        credential_written = False
        message += f'；登录态文件写入失败：{e}'
    return {
        'ok': True,
        'version': public['version'],
        'secrets': public['secrets'],
        'credential_written': credential_written,
        'message': message,
    }


_qr_lock = asyncio.Lock()
_qr_login = None


def _get_qr_login():
    global _qr_login
    if _qr_login is None:
        from media_parser.core.config import PluginConfig
        from media_parser.core.parsers.bilibili.login import BilibiliLogin

        _qr_login = BilibiliLogin(PluginConfig())
    return _qr_login


async def create_bilibili_qr() -> bytes:
    async with _qr_lock:
        return await _get_qr_login().login_with_qrcode()


async def poll_bilibili_qr() -> dict:
    async with _qr_lock:
        login = _get_qr_login()
        try:
            result = await login.poll_qr_state()
        except Exception as e:
            return {'status': 'error', 'message': f'读取二维码状态失败：{e}'}
        if result.get('status') != 'success':
            return result

        cookies = result.get('cookies') or {}
        refresh_token = result.get('refresh_token') or ''
        messages: list[str] = []
        try:
            login.save_cookies(cookies, refresh_token)
        except Exception as e:
            messages.append(f'登录态文件写入失败：{e}')
        public = None
        try:
            public = save_bilibili_config(cookies)
        except Exception as e:
            messages.append(f'配置保存失败：{e}')
        message = '登录成功'
        if messages:
            message += '；' + '；'.join(messages)
        response = {'status': 'success', 'message': message}
        if public:
            response['version'] = public['version']
            response['secrets'] = public['secrets']
        return response
