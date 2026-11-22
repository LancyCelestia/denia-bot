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


class XhsClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_accepts_a_real_png_and_rejects_garbage(self):
        response = {'id': 'a' * 64, 'qr_png': VALID_QR, 'expires_in': 180}
        with patch.object(xhs_login, '_request', AsyncMock(return_value=response)):
            started = await xhs_login.start('admin')
        self.assertEqual(started['id'], 'a' * 64)
        self.assertEqual(started['qr_png'], VALID_QR)
        with patch.object(
            xhs_login, '_request',
            AsyncMock(return_value={**response, 'qr_png': 'AAAA'}),
        ):
            with self.assertRaises(xhs_login.XhsLoginError):
                await xhs_login.start('admin')

    async def test_status_only_exposes_known_error_codes(self):
        with patch.object(
            xhs_login, '_request',
            AsyncMock(return_value={'status': 'error', 'code': 'risk_control'}),
        ):
            result = await xhs_login.status('admin', 'a' * 64)
        self.assertEqual(result, {'status': 'error', 'code': 'risk_control'})
        with patch.object(
            xhs_login, '_request',
            AsyncMock(return_value={'status': 'error', 'code': 'other'}),
        ):
            result = await xhs_login.status('admin', 'a' * 64)
        self.assertEqual(result, {'status': 'error'})


class XhsTokenTest(unittest.TestCase):
    def test_init_token_is_one_line_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'token.env'
            with patch.object(xhs_login, 'TOKEN_PATH', target):
                path = xhs_login.init_token()
                first = path.read_text(encoding='utf-8')
                self.assertRegex(first, r'^XHS_LOGIN_TOKEN=[0-9a-f]{64}\n$')
                self.assertEqual(xhs_login.init_token(), path)
                self.assertEqual(path.read_text(encoding='utf-8'), first)
                if os.name != 'nt':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                target.write_text('bad\n', encoding='utf-8')
                with self.assertRaises(xhs_login.XhsLoginError):
                    xhs_login.init_token()

    def test_cookie_header_validation(self):
        self.assertEqual(
            xhs_login._cookie_header('web_session=real; a1=device'),
            'web_session=real; a1=device',
        )
        with self.assertRaises(xhs_login.XhsLoginError):
            xhs_login._cookie_header('a1=device')
        with self.assertRaises(xhs_login.XhsLoginError):
            xhs_login._cookie_header('web_session=bad value')


if __name__ == '__main__':
    unittest.main()
