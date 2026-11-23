import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import dashboard as d
import parser_settings
import xhs_login
from media_parser.core.config import PluginConfig
from media_parser.core.cookie import CookieJar

VALID_QR = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A'
    'AwMCAO+/p9sAAAAASUVORK5CYII='
)


class XhsLoginDashboardTest(unittest.TestCase):
    def _login(self, client, tmp):
        d.AUTH_FILE = Path(tmp) / 'auth'
        d.set_password('a-strong-test-password')
        login = client.post('/api/login', json={'password': 'a-strong-test-password'})
        return {
            'Origin': 'http://testserver',
            'X-CSRF-Token': login.json()['csrf'],
        }, login.json()['csrf']

    def _seed(self, path):
        config = PluginConfig(path)
        config.parser.xhs.cookies = 'web_session=old; a1=old'
        config.parser.bilibili.video_quality = '_720P'
        config.save_config()

    def _platform(self, name):
        return next(
            entry for entry in parser_settings.load()['parsers_template']
            if entry['__template_key'] == name
        )

    def test_login_routes_require_auth_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            with TestClient(d.app) as client:
                self.assertEqual(client.post('/api/parser/xhs-login/start').status_code, 401)
                headers, _ = self._login(client, tmp)
                self.assertEqual(client.post('/api/parser/xhs-login/start').status_code, 403)
                with patch.object(
                    xhs_login, 'start',
                    AsyncMock(return_value={'id': 'a' * 64, 'qr_png': 'png', 'expires_in': 180}),
                ):
                    response = client.post('/api/parser/xhs-login/start', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['id'], 'a' * 64)
                self.assertNotIn('web_session', response.text)

    def test_status_and_cancel_bind_the_admin_session(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            with TestClient(d.app) as client:
                headers, token = self._login(client, tmp)
                status = AsyncMock(return_value={'status': 'ready'})
                cancel = AsyncMock(return_value={'ok': True})
                with patch.object(xhs_login, 'status', status), \
                        patch.object(xhs_login, 'cancel', cancel):
                    response = client.post(
                        '/api/parser/xhs-login/status',
                        json={'id': 'a' * 64}, headers=headers,
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'status': 'ready'})
                    cancel_response = client.post(
                        '/api/parser/xhs-login/cancel',
                        json={'id': 'a' * 64}, headers=headers,
                    )
                    self.assertEqual(cancel_response.status_code, 200)
                status.assert_awaited_once_with(token, 'a' * 64)
                cancel.assert_awaited_once_with(token, 'a' * 64)
                self.assertEqual(
                    client.post(
                        '/api/parser/xhs-login/status', json={'id': 'bad'}, headers=headers,
                    ).status_code,
                    400,
                )

    def test_save_claims_scan_cookie_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            path = Path(tmp) / 'parser.json'
            self._seed(path)
            cookies_dir = Path(tmp) / 'cookies'
            cookies_dir.mkdir(parents=True)
            CookieJar(SimpleNamespace(cookie_dir=cookies_dir), PluginConfig(path).parser.xhs, 'xiaohongshu.com')
            data = parser_settings.public()
            payload = {
                'version': data['version'],
                'general': {},
                'platforms': {'bilibili': {'video_quality': '_1080P'}},
                'clear_secrets': [],
                'xhs_login_id': 'a' * 64,
            }
            claim = AsyncMock(return_value={
                'claim_id': 'b' * 64,
                'cookies': 'web_session=real; a1=device',
            })
            settle = AsyncMock()
            with TestClient(d.app) as client:
                headers, token = self._login(client, tmp)
                with patch.object(xhs_login, 'claim', claim), \
                        patch.object(xhs_login, 'settle', settle):
                    response = client.post('/api/parser', json=payload, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('web_session=real', response.text)
            self.assertEqual(self._platform('xhs')['cookies'], 'web_session=real; a1=device')
            self.assertEqual(self._platform('bilibili')['video_quality'], '_1080P')
            jar = CookieJar(
                SimpleNamespace(cookie_dir=cookies_dir),
                PluginConfig(path).parser.xhs,
                'xiaohongshu.com',
            )
            self.assertEqual(jar.get()['web_session'], 'real')
            claim.assert_awaited_once_with(token, 'a' * 64)
            settle.assert_awaited_once_with(token, 'a' * 64, 'b' * 64, True)

    def test_version_conflict_releases_claim_and_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            path = Path(tmp) / 'parser.json'
            self._seed(path)
            claim = AsyncMock(return_value={
                'claim_id': 'b' * 64,
                'cookies': 'web_session=real',
            })
            settle = AsyncMock()
            with TestClient(d.app) as client:
                headers, token = self._login(client, tmp)
                stale = {
                    'version': '0' * 64,
                    'general': {},
                    'platforms': {},
                    'xhs_login_id': 'a' * 64,
                }
                with patch.object(xhs_login, 'claim', claim), \
                        patch.object(xhs_login, 'settle', settle):
                    response = client.post('/api/parser', json=stale, headers=headers)
                    self.assertEqual(response.status_code, 409)
                self.assertEqual(self._platform('xhs')['cookies'], 'web_session=old; a1=old')
                settle.assert_awaited_once_with(token, 'a' * 64, 'b' * 64, False)

                fresh = dict(stale)
                fresh['version'] = parser_settings.public()['version']
                with patch.object(xhs_login, 'claim', claim), \
                        patch.object(xhs_login, 'settle', settle):
                    retry = client.post('/api/parser', json=fresh, headers=headers)
                self.assertEqual(retry.status_code, 200, retry.text)
                self.assertEqual(self._platform('xhs')['cookies'], 'web_session=real')
                self.assertEqual(settle.await_args_list[-1].args[-1], True)

    def test_scan_cannot_race_manual_cookie_or_clear(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            claim = AsyncMock()
            with TestClient(d.app) as client:
                headers, _ = self._login(client, tmp)
                version = parser_settings.public()['version']
                with patch.object(xhs_login, 'claim', claim):
                    manual = client.post('/api/parser', json={
                        'version': version,
                        'general': {},
                        'platforms': {'xhs': {'cookies': 'manual'}},
                        'xhs_login_id': 'a' * 64,
                    }, headers=headers)
                    cleared = client.post('/api/parser', json={
                        'version': version,
                        'general': {},
                        'platforms': {},
                        'clear_secrets': ['xhs.cookies'],
                        'xhs_login_id': 'a' * 64,
                    }, headers=headers)
                    malformed = client.post('/api/parser', json={
                        'version': version,
                        'general': {},
                        'platforms': {'xhs': 'bad'},
                        'xhs_login_id': 'a' * 64,
                    }, headers=headers)
            self.assertEqual(manual.status_code, 400)
            self.assertEqual(cleared.status_code, 400)
            self.assertEqual(malformed.status_code, 400)
            claim.assert_not_awaited()

    def test_service_outage_is_reported_without_changing_config(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            path = Path(tmp) / 'parser.json'
            self._seed(path)
            with TestClient(d.app) as client:
                headers, _ = self._login(client, tmp)
                version = parser_settings.public()['version']
                with patch.object(
                    xhs_login, 'claim',
                    AsyncMock(side_effect=xhs_login.XhsLoginError(503, '登录服务不可用')),
                ):
                    response = client.post('/api/parser', json={
                        'version': version,
                        'general': {},
                        'platforms': {},
                        'xhs_login_id': 'a' * 64,
                    }, headers=headers)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self._platform('xhs')['cookies'], 'web_session=old; a1=old')

    def test_cleanup_failure_reports_pending_without_leaking_cookie(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(parser_settings, 'PATH', Path(tmp) / 'parser.json'), \
                patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
            path = Path(tmp) / 'parser.json'
            self._seed(path)
            with TestClient(d.app) as client:
                headers, _ = self._login(client, tmp)
                payload = {
                    'version': parser_settings.public()['version'],
                    'general': {},
                    'platforms': {},
                    'xhs_login_id': 'a' * 64,
                }
                with patch.object(
                    xhs_login, 'claim',
                    AsyncMock(return_value={'claim_id': 'b' * 64, 'cookies': 'web_session=real'}),
                ), patch.object(
                    xhs_login, 'settle',
                    AsyncMock(side_effect=xhs_login.XhsLoginError(503, 'cleanup failed')),
                ):
                    response = client.post('/api/parser', json=payload, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json().get('qr_cleanup_pending'))
            self.assertNotIn('web_session=real', response.text)


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def login_cookies(value: str = 'real') -> list[dict[str, str]]:
    return [{
        'name': 'web_session',
        'value': value,
        'domain': '.xiaohongshu.com',
        'path': '/',
    }]


class FakeBrowser:
    def __init__(self, qr: str = VALID_QR, steps=None, open_error=None):
        self.qr = qr
        self.steps = list(steps or [{'status': 'waiting'}])
        self.open_error = open_error
        self.closed = False

    async def open(self):
        if self.open_error is not None:
            raise self.open_error
        return self.qr

    async def poll(self):
        if not self.steps:
            return {'status': 'waiting'}
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def close(self):
        self.closed = True


class XhsManagerTest(unittest.IsolatedAsyncioTestCase):
    def manager(self, browser, clock=None):
        return xhs_login._LoginManager(
            browser_factory=lambda: browser,
            clock=clock or FakeClock(),
        )

    async def test_start_wait_scan_ready_claim_and_commit(self):
        browser = FakeBrowser(steps=[
            {'status': 'waiting'},
            {'status': 'scanned'},
            {'status': 'ready', 'cookies': login_cookies()},
        ])
        manager = self.manager(browser)
        started = await manager.start('admin')
        self.assertTrue(xhs_login.valid_session_id(started['id']))
        self.assertEqual(started['qr_png'], VALID_QR)
        self.assertEqual(started['expires_in'], xhs_login.QR_TTL)
        session_id = started['id']
        self.assertEqual(await manager.status('admin', session_id), {'status': 'waiting'})
        self.assertEqual(await manager.status('admin', session_id), {'status': 'scanned'})
        self.assertEqual(await manager.status('admin', session_id), {'status': 'ready'})
        self.assertTrue(browser.closed)
        claim = await manager.claim('admin', session_id)
        self.assertTrue(xhs_login.valid_session_id(claim['claim_id']))
        self.assertEqual(claim['cookies'], 'web_session=real')
        self.assertEqual(await manager.status('admin', session_id), {'status': 'saving'})
        await manager.settle('admin', session_id, claim['claim_id'], True)
        with self.assertRaises(xhs_login.XhsLoginError) as ctx:
            await manager.status('admin', session_id)
        self.assertEqual(ctx.exception.status, 404)

    async def test_owner_binding_and_single_active_session(self):
        browser = FakeBrowser()
        manager = self.manager(browser)
        started = await manager.start('admin-a')
        with self.assertRaises(xhs_login.XhsLoginError) as ctx:
            await manager.status('admin-b', started['id'])
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(xhs_login.XhsLoginError) as ctx:
            await manager.start('admin-a')
        self.assertEqual(ctx.exception.status, 409)
        await manager.cancel('admin-a', started['id'])
        self.assertTrue(browser.closed)
        with self.assertRaises(xhs_login.XhsLoginError):
            await manager.status('admin-a', started['id'])

    async def test_cancel_is_rejected_while_saving_and_release_restores_ready(self):
        browser = FakeBrowser(steps=[
            {'status': 'ready', 'cookies': login_cookies()},
        ])
        manager = self.manager(browser)
        started = await manager.start('admin')
        self.assertEqual(await manager.status('admin', started['id']), {'status': 'ready'})
        claim = await manager.claim('admin', started['id'])
        with self.assertRaises(xhs_login.XhsLoginError) as ctx:
            await manager.cancel('admin', started['id'])
        self.assertEqual(ctx.exception.status, 409)
        await manager.settle('admin', started['id'], claim['claim_id'], False)
        self.assertEqual(await manager.status('admin', started['id']), {'status': 'ready'})
        await manager.cancel('admin', started['id'])
        self.assertTrue(browser.closed)

    async def test_qr_and_ready_ttl_expire(self):
        clock = FakeClock(0.0)
        browser = FakeBrowser()
        manager = self.manager(browser, clock)
        started = await manager.start('admin')
        clock.now = xhs_login.QR_TTL
        self.assertEqual(await manager.status('admin', started['id']), {'status': 'expired'})
        self.assertTrue(browser.closed)

        clock.now = 1000.0
        browser2 = FakeBrowser(steps=[
            {'status': 'ready', 'cookies': login_cookies()},
        ])
        manager2 = self.manager(browser2, clock)
        started2 = await manager2.start('admin')
        self.assertEqual(await manager2.status('admin', started2['id']), {'status': 'ready'})
        clock.now += xhs_login.READY_TTL
        self.assertEqual(await manager2.status('admin', started2['id']), {'status': 'expired'})

    async def test_risk_control_on_start_is_a_clear_error(self):
        browser = FakeBrowser(open_error=xhs_login.XhsLoginError(
            502, xhs_login.RISK_MESSAGE,
        ))
        manager = self.manager(browser)
        with self.assertRaises(xhs_login.XhsLoginError) as ctx:
            await manager.start('admin')
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn('风控', ctx.exception.detail)
        self.assertTrue(browser.closed)

    async def test_risk_control_and_unknown_poll_errors_are_mapped(self):
        browser = FakeBrowser(steps=[{'status': 'error', 'code': 'risk_control'}])
        manager = self.manager(browser)
        started = await manager.start('admin')
        self.assertEqual(
            await manager.status('admin', started['id']),
            {'status': 'error', 'code': 'risk_control'},
        )
        self.assertTrue(browser.closed)

        browser2 = FakeBrowser(steps=[{'status': 'error', 'code': 'other'}])
        manager2 = self.manager(browser2)
        started2 = await manager2.start('admin')
        self.assertEqual(
            await manager2.status('admin', started2['id']),
            {'status': 'error', 'code': 'upstream_error'},
        )

    async def test_poll_exception_closes_browser_without_leaking_details(self):
        browser = FakeBrowser(steps=[RuntimeError('upstream secret')])
        manager = self.manager(browser)
        started = await manager.start('admin')
        self.assertEqual(
            await manager.status('admin', started['id']),
            {'status': 'error', 'code': 'upstream_error'},
        )
        self.assertTrue(browser.closed)

    async def test_invalid_cookie_is_reported_without_values(self):
        browser = FakeBrowser(steps=[{
            'status': 'ready',
            'cookies': [{
                'name': 'a1', 'value': 'device',
                'domain': '.xiaohongshu.com', 'path': '/',
            }],
        }])
        manager = self.manager(browser)
        started = await manager.start('admin')
        result = await manager.status('admin', started['id'])
        self.assertEqual(result, {'status': 'error', 'code': 'invalid_cookie'})
        self.assertNotIn('device', str(result))
        self.assertTrue(browser.closed)

    async def test_bad_qr_is_rejected_and_browser_is_closed(self):
        for qr in ('AAAA', 'not-base64!'):
            browser = FakeBrowser(qr=qr)
            manager = self.manager(browser)
            with self.assertRaises(xhs_login.XhsLoginError):
                await manager.start('admin')
            self.assertTrue(browser.closed)

    async def test_claim_lease_restores_ready(self):
        clock = FakeClock(0.0)
        browser = FakeBrowser(steps=[
            {'status': 'ready', 'cookies': login_cookies()},
        ])
        manager = self.manager(browser, clock)
        started = await manager.start('admin')
        self.assertEqual(await manager.status('admin', started['id']), {'status': 'ready'})
        claim = await manager.claim('admin', started['id'])
        clock.now += xhs_login.CLAIM_TTL
        self.assertEqual(await manager.status('admin', started['id']), {'status': 'ready'})
        with self.assertRaises(xhs_login.XhsLoginError):
            await manager.settle('admin', started['id'], claim['claim_id'], True)

    def test_cookie_header_prefers_exact_host_and_requires_web_session(self):
        header = xhs_login.cookie_header([
            {'name': 'web_session', 'value': 'root', 'domain': '.xiaohongshu.com', 'path': '/'},
            {'name': 'web_session', 'value': 'host', 'domain': 'www.xiaohongshu.com', 'path': '/'},
            {'name': 'a1', 'value': 'device', 'domain': '.xiaohongshu.com', 'path': '/'},
            {'name': 'bad name', 'value': 'x', 'domain': '.xiaohongshu.com', 'path': '/'},
        ])
        self.assertEqual(header, 'web_session=host; a1=device')
        with self.assertRaises(xhs_login.XhsLoginError):
            xhs_login.cookie_header([
                {'name': 'a1', 'value': 'device', 'domain': '.xiaohongshu.com'},
            ])
        with self.assertRaises(xhs_login.XhsLoginError):
            xhs_login.cookie_header('web_session=real')


if __name__ == '__main__':
    unittest.main()
