"""Dashboard-hosted Xiaohongshu QR login.

The dashboard starts a headless Chromium through Playwright on demand.  There
is no separate login service, port or token: the browser is a child of the
dashboard process and is closed when the scan is cancelled, expires or is
saved.  Cookies stay in this process and are released only through
``claim()``, immediately before the parser settings save.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import render_client

log = logging.getLogger(__name__)

LOGIN_URL = 'https://www.xiaohongshu.com/explore'
ALLOWED_HOSTS = ('xiaohongshu.com', 'xhscdn.com', 'xhscdn.net')
QR_TTL = 180
READY_TTL = 300
CLAIM_TTL = 30
RISK_MARKERS = ('当前网络存在风险', '安全限制', '请通过验证', '300012')
RISK_MESSAGE = '小红书风控：当前网络存在风险，请更换网络或稍后重试'
MAX_COOKIES = 100
MAX_COOKIE_HEADER = 32768
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


def is_xhs_url(raw: str) -> bool:
    try:
        parsed = urlparse(str(raw))
    except ValueError:
        return False
    if parsed.scheme == 'data':
        return True
    if parsed.scheme == 'about':
        return parsed.geturl() == 'about:blank'
    if parsed.scheme != 'https':
        return False
    if parsed.port and parsed.port != 443:
        return False
    host = (parsed.hostname or '').lower()
    return any(host == base or host.endswith('.' + base) for base in ALLOWED_HOSTS)


def _is_risk_control(text: Any) -> bool:
    value = str(text or '')
    return any(marker in value for marker in RISK_MARKERS)


def _png_ok(data: bytes) -> bool:
    try:
        render_client.png_bytes(data)
    except Exception:
        return False
    return True


def _domain_rank(cookie: dict[str, Any]) -> int:
    domain = str(cookie.get('domain') or '').lstrip('.').lower()
    if domain == 'www.xiaohongshu.com':
        return 3
    if domain == 'xiaohongshu.com':
        return 2
    return 1


def cookie_header(cookies: Any) -> str:
    """Build the Cookie header from Playwright cookies, preferring host cookies."""
    if not isinstance(cookies, list):
        raise XhsLoginError(502, '登录页返回的 Cookie 格式无效')
    allowed: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get('domain') or '').lstrip('.').lower()
        if domain == 'xiaohongshu.com' or domain.endswith('.xiaohongshu.com'):
            allowed.append(cookie)
    allowed.sort(key=lambda item: (
        -_domain_rank(item),
        -len(str(item.get('domain') or '')),
        -len(str(item.get('path') or '/')),
    ))
    values: dict[str, str] = {}
    for cookie in allowed:
        name = cookie.get('name')
        value = cookie.get('value')
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if (not COOKIE_NAME.fullmatch(name) or not COOKIE_VALUE.fullmatch(value)
                or ';' in value):
            continue
        values.setdefault(name, value)
    if not values.get('web_session'):
        raise XhsLoginError(502, '登录页返回的 Cookie 缺少 web_session')
    if len(values) > MAX_COOKIES:
        raise XhsLoginError(502, '登录页返回的 Cookie 过多')
    header = '; '.join(f'{name}={value}' for name, value in values.items())
    if len(header) > MAX_COOKIE_HEADER:
        raise XhsLoginError(502, '登录页返回的 Cookie 过大')
    return header


def _sandbox_enabled() -> bool:
    configured = os.environ.get('QQBOT_XHS_LOGIN_SANDBOX')
    if configured is not None:
        return configured.strip().lower() not in ('0', 'false', 'no', 'off')
    # root cannot start Chromium's setuid/userns sandbox reliably; the service
    # therefore follows the same rule as the former host mode.
    return not (
        sys.platform.startswith('linux')
        and getattr(os, 'geteuid', lambda: 1)() == 0
    )


def _chrome_user_agent(browser_version: str, system: str | None = None) -> str:
    # XHS risk control rejects Playwright's default HeadlessChrome UA with
    # error 300012 even when the page and network are usable. Present a
    # desktop Chrome UA consistent with the bundled browser and host platform.
    major = str(browser_version).split('.')[0] or '0'
    system = system or sys.platform
    if system.startswith('win'):
        platform = 'Windows NT 10.0; Win64; x64'
    elif system == 'darwin':
        platform = 'Macintosh; Intel Mac OS X 10_15_7'
    else:
        platform = 'X11; Linux x86_64'
    return (
        f'Mozilla/5.0 ({platform}) AppleWebKit/537.36 '
        f'(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36'
    )


class XhsBrowser:
    """One Playwright browser/page pair for a single QR session."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._guest_sessions: set[str] = set()
        self._closed = False

    async def _page_text(self) -> str:
        try:
            return await self._page.locator('body').inner_text()
        except Exception:
            return ''

    async def _route(self, route: Any) -> None:
        try:
            if is_xhs_url(route.request.url):
                await route.continue_()
            else:
                await route.abort()
        except Exception:
            pass

    def _dismiss(self, dialog: Any) -> None:
        async def dismiss() -> None:
            try:
                await dialog.dismiss()
            except Exception:
                pass

        asyncio.create_task(dismiss())

    async def open(self) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise XhsLoginError(
                503,
                '缺少 Python Playwright；请运行 '
                '.venv/bin/python -m playwright install chromium',
            ) from exc
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                chromium_sandbox=_sandbox_enabled(),
            )
            self._context = await self._browser.new_context(
                user_agent=_chrome_user_agent(self._browser.version),
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                viewport={'width': 1280, 'height': 800},
                service_workers='block',
                accept_downloads=False,
            )
            await self._context.route('**/*', self._route)
            self._page = await self._context.new_page()
            self._page.set_default_timeout(30_000)
            self._page.on('dialog', self._dismiss)
            await self._page.goto(
                LOGIN_URL, wait_until='domcontentloaded', timeout=30_000
            )

            qr = self._page.locator('.login-container img.qrcode-img')
            try:
                await qr.wait_for(state='visible', timeout=5_000)
            except Exception:
                if _is_risk_control(await self._page_text()):
                    raise XhsLoginError(502, RISK_MESSAGE)
                login = self._page.get_by_role('button', name='登录').first
                try:
                    if await login.count():
                        await login.click()
                except Exception:
                    pass
                try:
                    await qr.wait_for(state='visible', timeout=25_000)
                except Exception:
                    if _is_risk_control(await self._page_text()):
                        raise XhsLoginError(502, RISK_MESSAGE)
                    raise XhsLoginError(502, '小红书登录页未出现二维码，请稍后重试')

            await self._page.wait_for_function(
                """() => {
                    const image = document.querySelector('.login-container img.qrcode-img');
                    return Boolean(image && image.complete && image.naturalWidth > 0);
                }""",
                timeout=10_000,
            )
            cookies = await self._context.cookies('https://www.xiaohongshu.com/')
            self._guest_sessions = {
                str(cookie['value'])
                for cookie in cookies
                if cookie.get('name') == 'web_session' and cookie.get('value')
            }
            png = await qr.screenshot(type='png')
            if not _png_ok(png):
                raise XhsLoginError(502, '登录页没有返回有效二维码')
            return base64.b64encode(png).decode('ascii')
        except XhsLoginError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise XhsLoginError(502, '打开小红书登录页失败，请稍后重试') from exc

    async def poll(self) -> dict[str, Any]:
        if self._closed or self._page is None or self._page.is_closed():
            return {'status': 'error', 'code': 'upstream_error'}
        body = await self._page_text()
        if _is_risk_control(body):
            return {'status': 'error', 'code': 'risk_control'}
        try:
            cookies = await self._context.cookies('https://www.xiaohongshu.com/')
            current = next(
                (cookie for cookie in cookies
                 if cookie.get('name') == 'web_session' and cookie.get('value')),
                None,
            )
            logged_in = await self._page.locator(
                '.main-container .user .link-wrapper .channel'
            ).count()
        except Exception:
            return {'status': 'error', 'code': 'upstream_error'}
        if (logged_in > 0 and current
                and str(current['value']) not in self._guest_sessions):
            return {'status': 'ready', 'cookies': cookies}
        if '二维码已过期' in body:
            return {'status': 'expired'}
        if '扫码成功' in body:
            return {'status': 'scanned'}
        return {'status': 'waiting'}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        context, browser, playwright = self._context, self._browser, self._playwright
        self._context = self._browser = self._playwright = self._page = None
        self._guest_sessions.clear()
        for closer in (
            context.close if context is not None else None,
            browser.close if browser is not None else None,
            playwright.stop if playwright is not None else None,
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                pass


@dataclass
class _Session:
    id: str
    owner: str
    status: str
    qr_png: str = ''
    cookies: str = ''
    code: str = ''
    claim_id: str = ''
    expires_at: float = 0.0
    ready_until: float = 0.0
    claim_until: float = 0.0
    browser: Any = None


class _LoginManager:
    def __init__(
        self,
        browser_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._browser_factory = browser_factory or XhsBrowser
        self._clock = clock
        self._session: _Session | None = None
        self._mutex: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._mutex is None or self._loop is not loop:
            self._mutex = asyncio.Lock()
            self._loop = loop
        return self._mutex

    def _now(self) -> float:
        return float(self._clock())

    @staticmethod
    def _result(status: str, code: str = '') -> dict[str, Any]:
        result: dict[str, Any] = {'status': status}
        if status == 'error' and code in ERROR_CODES:
            result['code'] = code
        return result

    async def _close_browser(self, session: _Session) -> None:
        browser = session.browser
        session.browser = None
        if browser is None:
            return
        try:
            await browser.close()
        except Exception:
            log.exception('xhs login browser close failed')

    async def _close(self, session: _Session | None) -> None:
        if session is None:
            return
        await self._close_browser(session)
        if self._session is session:
            self._session = None

    def _get(self, admin_session: str, session_id: str) -> _Session:
        if not valid_session_id(session_id):
            raise XhsLoginError(400, '扫码会话无效')
        session = self._session
        if (session is None or session.id != session_id
                or session.owner != owner(admin_session)):
            raise XhsLoginError(404, '扫码会话不存在或已过期')
        return session

    async def start(self, admin_session: str) -> dict[str, Any]:
        async with self._lock():
            current = self._session
            now = self._now()
            if current is not None:
                finished = current.status in ('expired', 'error') or (
                    current.status in ('waiting', 'scanned')
                    and now >= current.expires_at
                ) or (
                    current.status == 'ready' and now >= current.ready_until
                )
                if not finished:
                    raise XhsLoginError(409, '扫码会话已在进行中，请先取消')
                await self._close(current)

            session = _Session(
                id=secrets.token_hex(32),
                owner=owner(admin_session),
                status='starting',
            )
            self._session = session
            try:
                browser = self._browser_factory()
                session.browser = browser
                qr_png = await browser.open()
            except asyncio.CancelledError:
                await asyncio.shield(self._close(session))
                raise
            except XhsLoginError:
                await self._close(session)
                raise
            except Exception as exc:
                log.exception('xhs login browser open failed')
                await self._close(session)
                raise XhsLoginError(502, '生成小红书二维码失败，请稍后重试') from exc

            try:
                png = base64.b64decode(qr_png, validate=True)
            except (TypeError, ValueError) as exc:
                await self._close(session)
                raise XhsLoginError(502, '登录页返回的二维码无效') from exc
            if not _png_ok(png):
                await self._close(session)
                raise XhsLoginError(502, '登录页返回的二维码无效')

            session.qr_png = qr_png
            session.status = 'waiting'
            session.expires_at = self._now() + QR_TTL
            return {
                'id': session.id,
                'qr_png': qr_png,
                'expires_in': QR_TTL,
            }

    async def status(self, admin_session: str, session_id: str) -> dict[str, Any]:
        async with self._lock():
            session = self._get(admin_session, session_id)
            now = self._now()
            if session.status in ('waiting', 'scanned'):
                if now >= session.expires_at:
                    await self._close(session)
                    session.status = 'expired'
                    return self._result('expired')
                try:
                    result = await session.browser.poll()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception('xhs login poll failed')
                    await self._close_browser(session)
                    session.status = 'error'
                    session.code = 'upstream_error'
                    return self._result('error', 'upstream_error')

                state = result.get('status') if isinstance(result, dict) else None
                if state == 'ready':
                    try:
                        header = cookie_header(result.get('cookies'))
                    except XhsLoginError:
                        await self._close_browser(session)
                        session.status = 'error'
                        session.code = 'invalid_cookie'
                        return self._result('error', 'invalid_cookie')
                    session.cookies = header
                    session.status = 'ready'
                    session.ready_until = self._now() + READY_TTL
                    await self._close_browser(session)
                    return self._result('ready')
                if state == 'error':
                    code = result.get('code')
                    if code not in ERROR_CODES:
                        code = 'upstream_error'
                    await self._close_browser(session)
                    session.status = 'error'
                    session.code = code
                    return self._result('error', code)
                if state == 'expired':
                    await self._close(session)
                    session.status = 'expired'
                    return self._result('expired')
                if state == 'scanned':
                    session.status = 'scanned'
                    return self._result('scanned')
                return self._result('waiting')

            if session.status == 'ready':
                if now >= session.ready_until:
                    await self._close(session)
                    session.status = 'expired'
                    return self._result('expired')
                return self._result('ready')
            if session.status == 'saving':
                if now >= session.claim_until:
                    if now < session.ready_until:
                        session.status = 'ready'
                        session.claim_id = ''
                        session.claim_until = 0
                        return self._result('ready')
                    await self._close(session)
                    session.status = 'expired'
                    return self._result('expired')
                return self._result('saving')
            return self._result(session.status, session.code)

    async def cancel(self, admin_session: str, session_id: str) -> dict[str, Any]:
        async with self._lock():
            session = self._get(admin_session, session_id)
            if session.status == 'saving':
                raise XhsLoginError(409, '扫码结果正在保存，请稍候')
            await self._close(session)
            session.status = 'expired'
            return {'ok': True}

    async def claim(self, admin_session: str, session_id: str) -> dict[str, str]:
        async with self._lock():
            session = self._get(admin_session, session_id)
            now = self._now()
            if session.status == 'saving':
                raise XhsLoginError(409, '扫码结果正在保存，请稍候')
            if session.status != 'ready':
                raise XhsLoginError(409, '扫码尚未完成或已过期')
            if now >= session.ready_until:
                await self._close(session)
                session.status = 'expired'
                raise XhsLoginError(404, '扫码会话不存在或已过期')
            claim_id = secrets.token_hex(32)
            session.claim_id = claim_id
            session.claim_until = now + CLAIM_TTL
            session.status = 'saving'
            return {'claim_id': claim_id, 'cookies': session.cookies}

    async def settle(
        self,
        admin_session: str,
        session_id: str,
        claim_id: str,
        commit: bool,
    ) -> None:
        async with self._lock():
            if not valid_session_id(claim_id):
                raise XhsLoginError(400, '扫码会话无效')
            session = self._get(admin_session, session_id)
            if session.status != 'saving' or session.claim_id != claim_id:
                raise XhsLoginError(409, '扫码结果已保存或已过期')
            if commit:
                await self._close(session)
                session.status = 'expired'
                return
            if self._now() < session.ready_until:
                session.status = 'ready'
                session.claim_id = ''
                session.claim_until = 0
                return
            await self._close(session)
            session.status = 'expired'

    async def close(self) -> None:
        async with self._lock():
            await self._close(self._session)


_manager = _LoginManager()


async def start(admin_session: str) -> dict[str, Any]:
    return await _manager.start(admin_session)


async def status(admin_session: str, session_id: str) -> dict[str, Any]:
    return await _manager.status(admin_session, session_id)


async def cancel(admin_session: str, session_id: str) -> dict[str, Any]:
    return await _manager.cancel(admin_session, session_id)


async def claim(admin_session: str, session_id: str) -> dict[str, str]:
    return await _manager.claim(admin_session, session_id)


async def settle(
    admin_session: str,
    session_id: str,
    claim_id: str,
    commit: bool,
) -> None:
    await _manager.settle(admin_session, session_id, claim_id, commit)


async def close() -> None:
    await _manager.close()
