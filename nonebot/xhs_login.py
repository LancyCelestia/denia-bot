"""Dashboard client for the isolated Xiaohongshu QR-login service.

The dashboard process never talks to Xiaohongshu directly.  It asks the
separate, loopback-only login container for a QR code, then claims the
resulting cookies when the user saves the parser settings.  Cookies are never
returned to the browser.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx

import render_client
import render_settings

ROOT = Path(__file__).resolve().parent
TOKEN_PATH = ROOT / 'data' / 'xhs-login-token.env'
DEFAULT_URL = 'http://127.0.0.1:3102'
TOKEN_NAME = 'XHS_LOGIN_TOKEN'
MAX_RESPONSE = 256 * 1024
HEX64 = re.compile(r'^[0-9a-f]{64}$')
COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
COOKIE_VALUE = re.compile(r'^[\x21-\x7e]+$')
STATES = {'waiting', 'scanned', 'ready', 'saving', 'expired', 'error'}
ERROR_CODES = {'risk_control', 'upstream_error', 'invalid_cookie'}


class XhsLoginError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def valid_session_id(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def owner(admin_session: str) -> str:
    return hashlib.sha256(str(admin_session).encode('utf-8')).hexdigest()


def _base_url() -> str:
    value = os.environ.get('QQBOT_XHS_LOGIN_URL') or DEFAULT_URL
    try:
        return render_settings.validate_url(value)
    except ValueError as exc:
        raise XhsLoginError(503, f'小红书登录服务地址无效：{exc}') from exc


def _token() -> str:
    try:
        text = TOKEN_PATH.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise XhsLoginError(
            503, '小红书登录服务未配置，请先运行 python nonebot/xhs_login.py --init-token'
        ) from exc
    prefix = TOKEN_NAME + '='
    if not text.startswith(prefix):
        raise XhsLoginError(503, '小红书登录服务令牌文件格式无效')
    token = text[len(prefix):].strip()
    if not HEX64.fullmatch(token):
        raise XhsLoginError(503, '小红书登录服务令牌格式无效')
    return token


def init_token() -> Path:
    """Create the shared token once; never overwrite an existing file."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        _token()
        return TOKEN_PATH
    token = secrets.token_hex(32)
    try:
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _token()
        return TOKEN_PATH
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(f'{TOKEN_NAME}={token}\n')
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except OSError:
            pass
    except Exception:
        TOKEN_PATH.unlink(missing_ok=True)
        raise
    return TOKEN_PATH


def _error_for(status: int) -> XhsLoginError:
    if status == 404:
        return XhsLoginError(404, '扫码会话不存在或已过期')
    if status == 409:
        return XhsLoginError(409, '扫码会话正在保存或尚未就绪')
    if status == 400:
        return XhsLoginError(400, '扫码请求无效')
    if status in (401, 403):
        return XhsLoginError(503, '小红书登录服务令牌不一致')
    if status in (408, 504):
        return XhsLoginError(504, '小红书登录服务超时')
    return XhsLoginError(503, '小红书登录服务不可用')


async def _request(path: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    token = _token()
    url = _base_url() + path
    headers = {'X-XHS-Login-Token': token, 'Content-Type': 'application/json'}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            async with client.stream('POST', url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    raise _error_for(response.status_code)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise XhsLoginError(502, '小红书登录服务响应过大')
                    chunks.append(chunk)
        data = json.loads(b''.join(chunks))
    except XhsLoginError:
        raise
    except httpx.TimeoutException as exc:
        raise XhsLoginError(504, '小红书登录服务超时') from exc
    except (httpx.HTTPError, OSError) as exc:
        raise XhsLoginError(503, '小红书登录服务不可用，请确认独立登录容器已启动') from exc
    except (ValueError, TypeError) as exc:
        raise XhsLoginError(502, '小红书登录服务响应无效') from exc
    if not isinstance(data, dict):
        raise XhsLoginError(502, '小红书登录服务响应无效')
    return data


def _cookie_header(cookies: Any) -> str:
    if not isinstance(cookies, str):
        raise XhsLoginError(502, '登录服务返回的 Cookie 格式无效')
    text = cookies.strip()
    if not text:
        raise XhsLoginError(502, '登录服务返回了空 Cookie')
    values: dict[str, str] = {}
    for chunk in text.split(';'):
        name, separator, value = chunk.strip().partition('=')
        if (not separator or not COOKIE_NAME.fullmatch(name)
                or not COOKIE_VALUE.fullmatch(value) or ';' in value):
            raise XhsLoginError(502, '登录服务返回的 Cookie 格式无效')
        values.setdefault(name, value)
    if not values.get('web_session'):
        raise XhsLoginError(502, '登录服务返回的 Cookie 缺少 web_session')
    if len(values) > 100 or len(text) > 32768:
        raise XhsLoginError(502, '登录服务返回的 Cookie 过大')
    return '; '.join(f'{name}={value}' for name, value in values.items())


async def start(admin_session: str) -> dict[str, Any]:
    data = await _request('/start', {'owner': owner(admin_session)}, timeout=35)
    session_id = data.get('id')
    qr_png = data.get('qr_png')
    expires_in = data.get('expires_in')
    if not valid_session_id(session_id):
        raise XhsLoginError(502, '登录服务返回了无效会话')
    if not isinstance(qr_png, str) or not qr_png:
        raise XhsLoginError(502, '登录服务没有返回二维码')
    try:
        png = base64.b64decode(qr_png, validate=True)
        render_client.png_bytes(png)
    except Exception as exc:
        raise XhsLoginError(502, '登录服务返回的二维码无效') from exc
    if not isinstance(expires_in, int) or not 1 <= expires_in <= 600:
        expires_in = 180
    return {'id': session_id, 'qr_png': qr_png, 'expires_in': expires_in}


async def status(admin_session: str, session_id: str) -> dict[str, Any]:
    if not valid_session_id(session_id):
        raise XhsLoginError(400, '扫码会话无效')
    data = await _request(
        '/status', {'owner': owner(admin_session), 'id': session_id}, timeout=5
    )
    state = data.get('status')
    if state not in STATES:
        raise XhsLoginError(502, '登录服务返回了无效状态')
    result: dict[str, Any] = {'status': state}
    code = data.get('code')
    if state == 'error' and code in ERROR_CODES:
        result['code'] = code
    return result


async def cancel(admin_session: str, session_id: str) -> dict[str, Any]:
    if not valid_session_id(session_id):
        raise XhsLoginError(400, '扫码会话无效')
    await _request('/cancel', {'owner': owner(admin_session), 'id': session_id}, timeout=5)
    return {'ok': True}


async def claim(admin_session: str, session_id: str) -> dict[str, str]:
    if not valid_session_id(session_id):
        raise XhsLoginError(400, '扫码会话无效')
    data = await _request('/claim', {'owner': owner(admin_session), 'id': session_id}, timeout=5)
    claim_id = data.get('claim_id')
    if not valid_session_id(claim_id):
        raise XhsLoginError(502, '登录服务返回了无效领取凭证')
    return {'claim_id': claim_id, 'cookies': _cookie_header(data.get('cookies'))}


async def settle(admin_session: str, session_id: str, claim_id: str, commit: bool) -> None:
    if not valid_session_id(session_id) or not valid_session_id(claim_id):
        raise XhsLoginError(400, '扫码会话无效')
    await _request('/settle', {
        'owner': owner(admin_session),
        'id': session_id,
        'claim_id': claim_id,
        'commit': bool(commit),
    }, timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='小红书扫码登录服务令牌')
    parser.add_argument('--init-token', action='store_true', help='创建共享登录服务令牌')
    args = parser.parse_args(argv)
    if not args.init_token:
        parser.error('请使用 --init-token')
    try:
        path = init_token()
    except XhsLoginError as exc:
        print(f'错误：{exc.detail}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'错误：无法写入 {TOKEN_PATH}：{exc}', file=sys.stderr)
        return 1
    print(f'令牌文件已就绪：{path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
