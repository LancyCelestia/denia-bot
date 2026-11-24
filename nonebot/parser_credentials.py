"""Bilibili parser credential import and QR-login helpers for the dashboard.

The dashboard is a separate process from NoneBot. It writes the same
``data/media_parser/config.json`` and ``data/media_parser/cookies`` file that
the parser already owns; the running parser reloads the credential file when
its mtime changes, so a successful login does not need a bot restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

import parser_settings
from media_parser.core.cookie import (
    NETSCAPE_HEADER,
    NetscapeCookie,
    is_netscape_cookie_file,
    parse_netscape_cookie_line,
)

# The dashboard is a separate process from the bot. Add the parser's vendored
# dependency directory the same way plugins/media_parser.py does, so the
# Bilibili QR path works without installing parser extras into the app venv.
_PARSER_DEPS = Path(__file__).resolve().parent / 'parser_deps'
if _PARSER_DEPS.is_dir() and str(_PARSER_DEPS) not in sys.path:
    sys.path.append(str(_PARSER_DEPS))

BILI_KEYS = ('SESSDATA', 'bili_jct', 'DedeUserID')
BILI_REQUIRED = ('SESSDATA', 'bili_jct')
MAX_INPUT = 32768
MAX_VALUE = 4096

# Browser exports are filtered by the platform the credential belongs to. The
# parser's CookieJar owns the matching domain, so this table mirrors it; a
# cookies.txt may contain every domain the browser knows, but only the target
# site's rows are imported.
PLATFORM_DOMAINS = {
    'acfun': ('acfun.cn',),
    'allcpp': ('allcpp.cn',),
    'bilibili': ('bilibili.com', 'bilibili.tv'),
    'douyin': ('douyin.com', 'iesdouyin.com'),
    'instagram': ('instagram.com',),
    'kuaishou': ('kuaishou.com',),
    'ncm': ('music.163.com', '163.com'),
    'nga': ('nga.cn', '178.com'),
    'qzone': ('qq.com',),
    'shipinhao': ('tencent.com',),
    'tiktok': ('tiktok.com',),
    'twitter': ('x.com', 'twitter.com'),
    'weibo': ('weibo.com', 'weibo.cn'),
    'xhs': ('xiaohongshu.com',),
    'xiaoheihe': ('xiaoheihe.cn', 'heybox.com'),
    'youtube': ('youtube.com', 'google.com', 'googlevideo.com'),
    'zhihu': ('zhihu.com',),
}
PLATFORM_LABELS = {
    'bilibili': 'B 站',
    'xhs': '小红书',
    'youtube': 'YouTube',
    'twitter': 'Twitter / X',
    'instagram': 'Instagram',
    'weibo': '微博',
    'zhihu': '知乎',
    'qzone': 'QQ 空间',
}

_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


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


def _clean_cookie_input(raw: str) -> str:
    return (raw or '').strip().removeprefix('\ufeff').strip()


def _check_input_size(text: str, label: str = 'Cookie 内容') -> None:
    if len(text.encode('utf-8')) > MAX_INPUT:
        raise ValueError(f'{label}超过 32 KiB，请只导出目标站点的 Cookie')


def _platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def _platform_domains(platform: str) -> tuple[str, ...]:
    domains = PLATFORM_DOMAINS.get(platform)
    if not domains:
        raise ValueError('该平台不支持 Cookie 导入')
    return domains


def _domain_matches(domain: str, base: str) -> bool:
    value = domain.strip().lower().lstrip('.')
    root = base.strip().lower().lstrip('.')
    return value == root or value.endswith(f'.{root}')


def _matches_platform(domain: str, platform: str) -> bool:
    return any(
        _domain_matches(domain, base) for base in _platform_domains(platform)
    )


def _netscape_rows(text: str) -> list[NetscapeCookie]:
    rows: list[NetscapeCookie] = []
    for line in text.splitlines():
        cookie = parse_netscape_cookie_line(line)
        if cookie is not None:
            rows.append(cookie)
    return rows


def parse_platform_cookie_rows(raw: str, platform: str) -> list[NetscapeCookie]:
    """Parse cookies.txt / Cookie header for one platform.

    Netscape rows are filtered to the platform's own domains and expired rows
    are dropped. A Cookie header is accepted as a convenience for the dynamic
    dashboard fields that already use that format.
    """
    text = _clean_cookie_input(raw)
    if not text:
        raise ValueError('请粘贴 Cookie 或导入 cookies.txt')
    platform = (platform or '').strip().lower()
    domains = _platform_domains(platform)
    label = _platform_label(platform)
    _check_input_size(text, 'Cookie / cookies.txt ')

    if is_netscape_cookie_file(text):
        rows = _netscape_rows(text)
        if not rows:
            raise ValueError('cookies.txt 格式无效：没有可用的 Netscape Cookie 行')
        matching = [row for row in rows if _matches_platform(row.domain, platform)]
        if not matching:
            raise ValueError(
                f'Cookie 域名不匹配：cookies.txt 中没有{label}的 Cookie'
                f'（{", ".join(domains)}）'
            )
        now = int(time.time())
        fresh = [row for row in matching if row.expires == 0 or row.expires > now]
        if not fresh:
            raise ValueError(f'{label} Cookie 已全部过期，请重新导出 cookies.txt')
        return fresh

    return _header_cookie_rows(text, platform)


def _header_cookie_rows(text: str, platform: str) -> list[NetscapeCookie]:
    if text.lower().startswith('cookie:'):
        text = text[7:].strip()
    cookies: dict[str, str] = {}
    for chunk in re.split(r'[;\r\n]+', text):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition('=')
        name = name.strip()
        value = value.strip()
        if not sep:
            raise ValueError(f'Cookie 片段缺少 =：{chunk[:40]}')
        if not _COOKIE_NAME.fullmatch(name):
            raise ValueError('Cookie 名称无效')
        if not value:
            raise ValueError(f'Cookie 字段 {name} 为空')
        if len(value) > MAX_VALUE:
            raise ValueError(f'Cookie 字段 {name} 过长')
        if any(not 0x20 <= ord(ch) <= 0x7e or ch in ';\r\n\t' for ch in value):
            raise ValueError(f'Cookie 字段 {name} 含非法字符')
        cookies[name] = value
    if not cookies:
        raise ValueError('Cookie 内容为空')

    domain = f'.{_platform_domains(platform)[0]}'
    return [
        NetscapeCookie(
            domain=domain,
            include_subdomains=True,
            path='/',
            secure=True,
            expires=0,
            name=name,
            value=value,
        )
        for name, value in cookies.items()
    ]


def _cookie_header_from_rows(rows: list[NetscapeCookie]) -> str:
    ordered = sorted(rows, key=lambda row: len(row.path), reverse=True)
    values: dict[str, str] = {}
    for row in ordered:
        values.setdefault(row.name, row.value)
    header = '; '.join(f'{name}={value}' for name, value in values.items())
    if len(header.encode('utf-8')) > MAX_INPUT:
        raise ValueError('Cookie 内容过长')
    return header


def _require_bilibili(cookies: Mapping[str, str]) -> dict[str, str]:
    missing = [name for name in BILI_REQUIRED if not cookies.get(name)]
    if missing:
        raise ValueError('缺少 Cookie 字段：' + '、'.join(missing))
    return {name: cookies[name] for name in BILI_KEYS if cookies.get(name)}


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
    """Parse a Cookie header, JSON, or a browser-exported Netscape cookies.txt."""
    text = _clean_cookie_input(raw)
    if not text:
        raise ValueError('请粘贴 Cookie、JSON，或导入 cookies.txt')
    _check_input_size(text, 'Cookie / cookies.txt ')

    if is_netscape_cookie_file(text):
        cookies: dict[str, str] = {}
        for row in parse_platform_cookie_rows(text, 'bilibili'):
            name = _normalise_name(row.name)
            if name is not None:
                cookies[name] = _validate_value(name, row.value)
        return _require_bilibili(cookies)

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
    return _require_bilibili(cookies)


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


def _rows_from_cookie_mapping(
    cookies: Mapping[str, str], platform: str
) -> list[NetscapeCookie]:
    domain = f'.{_platform_domains(platform)[0]}'
    return [
        NetscapeCookie(
            domain=domain,
            include_subdomains=True,
            path='/',
            secure=True,
            expires=0,
            name=name,
            value=value,
        )
        for name, value in cookies.items()
    ]


def save_bilibili_cookies(
    cookies: Mapping[str, str],
    rows: list[NetscapeCookie] | None = None,
) -> dict:
    """Paste/JSON/Netscape import: save config, cookie file, then login state."""
    public = save_bilibili_config(cookies)
    message = 'B 站 Cookie 已导入'
    if rows is None:
        rows = _rows_from_cookie_mapping(
            {name: cookies[name] for name in BILI_KEYS if cookies.get(name)},
            'bilibili',
        )
    cookie_file_written = True
    try:
        write_platform_cookie_file('bilibili', rows)
    except OSError as e:
        cookie_file_written = False
        message += f'；Cookie 文件写入失败：{e.strerror or e}'
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
        'cookie_file_written': cookie_file_written,
        'credential_written': credential_written,
        'message': message,
    }


def parse_bilibili_cookie_rows(raw: str) -> list[NetscapeCookie] | None:
    """Return Netscape rows for Bilibili, or None for header/JSON input."""
    text = _clean_cookie_input(raw)
    if not text or not is_netscape_cookie_file(text):
        return None
    return parse_platform_cookie_rows(text, 'bilibili')


def _platform_supports_cookies(platform: str) -> bool:
    templates = (
        parser_settings.schema()
        .get('parsers_template', {})
        .get('templates', {})
    )
    items = (templates.get(platform) or {}).get('items') or {}
    return 'cookies' in items


def save_platform_config(platform: str, cookies: str) -> dict:
    """Write one platform's cookies through the existing parser secret path."""
    for attempt in range(2):
        data = parser_settings.public()
        if platform not in (data.get('platforms') or {}):
            raise ValueError('未找到该平台的解析器配置')
        payload = {
            'version': data['version'],
            'general': {},
            'platforms': {platform: {'cookies': cookies}},
            'clear_secrets': [],
        }
        try:
            return parser_settings.save(payload)
        except FileExistsError:
            if attempt:
                raise
    raise FileExistsError('配置已被其他操作修改，请刷新后重试')


def serialize_cookie_file(rows: list[NetscapeCookie]) -> str:
    lines = [
        NETSCAPE_HEADER,
        '# Generated by the qqbot dashboard cookie import; do not edit while the bot runs.',
        '',
    ]
    lines.extend(row.to_netscape_line() for row in rows)
    return '\n'.join(lines) + '\n'


def write_platform_cookie_file(platform: str, rows: list[NetscapeCookie]) -> Path:
    """Atomically replace ``data/media_parser/cookies/<platform>_cookies.txt``."""
    cookie_dir = parser_settings.PATH.parent / 'cookies'
    cookie_dir.mkdir(parents=True, exist_ok=True)
    target = cookie_dir / f'{platform}_cookies.txt'
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            'w',
            encoding='utf-8',
            newline='\n',
            dir=cookie_dir,
            prefix=f'.{platform}-',
            suffix='.tmp',
            delete=False,
        ) as handle:
            temp = handle.name
            handle.write(serialize_cookie_file(rows))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, target)
        temp = None
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    return target


def save_platform_cookies(platform: str, raw: str) -> dict:
    """Import cookies.txt (or a Cookie header) for any platform with a field."""
    platform = (platform or '').strip().lower()
    if platform not in PLATFORM_DOMAINS:
        raise ValueError('不支持该平台的 Cookie 导入')
    if not _platform_supports_cookies(platform):
        raise ValueError('该平台不支持 Cookie 导入')

    rows = parse_platform_cookie_rows(raw, platform)
    header = _cookie_header_from_rows(rows)
    public = save_platform_config(platform, header)

    label = _platform_label(platform)
    expirations = sorted(row.expires for row in rows if row.expires)
    earliest = expirations[0] if expirations else None
    message = f'{label} Cookie 已导入（{len(rows)} 条'
    if earliest:
        message += f'，最早到期 {time.strftime("%Y-%m-%d", time.localtime(earliest))}'
    message += '）'

    file_written = True
    try:
        write_platform_cookie_file(platform, rows)
    except OSError as e:
        file_written = False
        message += f'；Cookie 文件写入失败：{e.strerror or e}'

    return {
        'ok': True,
        'platform': platform,
        'version': public['version'],
        'secrets': public['secrets'],
        'count': len(rows),
        'earliest_expiry': earliest,
        'file_written': file_written,
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
