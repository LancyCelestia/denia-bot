"""Dashboard API tests for Bilibili credential import and QR login."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import dashboard as d
import parser_credentials
import parser_settings


class ParserCredentialApiTest(unittest.TestCase):
    def _login(self, tmp):
        d.AUTH_FILE = Path(tmp) / 'auth'
        d.set_password('a-strong-test-password')
        client = TestClient(d.app)
        login = client.post('/api/login', json={'password': 'a-strong-test-password'})
        headers = {
            'Origin': 'http://testserver',
            'X-CSRF-Token': login.json()['csrf'],
        }
        return client, headers

    def test_cookie_import_requires_auth_csrf_and_valid_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            with patch.object(parser_settings, 'PATH', config), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'), \
                 patch.object(parser_credentials, 'write_bilibili_credential') as write:
                payload = {'cookies': 'SESSDATA=sess; bili_jct=jct; DedeUserID=123'}
                self.assertEqual(
                    TestClient(d.app)
                    .post('/api/parser/bilibili/cookies', json=payload)
                    .status_code,
                    401,
                )
                client, headers = self._login(tmp)
                self.assertEqual(
                    client.post(
                        '/api/parser/bilibili/cookies',
                        json={'cookies': 'SESSDATA=sess'},
                        headers=headers,
                    ).status_code,
                    400,
                )
                self.assertFalse(config.exists())
                self.assertFalse(
                    client.get('/api/parser').json()['secrets']['bilibili.cookies']
                )
                response = client.post(
                    '/api/parser/bilibili/cookies', json=payload, headers=headers
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['secrets']['bilibili.cookies'])
                write.assert_called_once()
                entries = {
                    item['__template_key']: item
                    for item in parser_settings.load()['parsers_template']
                }
                self.assertIn('SESSDATA=sess', entries['bilibili']['cookies'])
                self.assertTrue(
                    client.get('/api/parser').json()['secrets']['bilibili.cookies']
                )

    def test_generic_cookie_import_requires_auth_csrf_and_filters_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            far = int(time.time()) + 86400
            raw = (
                '# Netscape HTTP Cookie File\n'
                f'#HttpOnly_.xiaohongshu.com\tTRUE\t/\tTRUE\t{far}\tweb_session\tsecret-xhs\n'
                '.xiaohongshu.com\tTRUE\t/\tFALSE\t0\ta1\tabc\n'
            )
            with patch.object(parser_settings, 'PATH', config), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'):
                payload = {'platform': 'xhs', 'cookies': raw}
                self.assertEqual(
                    TestClient(d.app).post('/api/parser/cookies', json=payload).status_code,
                    401,
                )
                client, headers = self._login(tmp)
                self.assertEqual(
                    client.post('/api/parser/cookies', json=payload).status_code, 403
                )
                self.assertEqual(
                    client.post(
                        '/api/parser/cookies',
                        json={'platform': 'unknown', 'cookies': raw},
                        headers=headers,
                    ).status_code,
                    400,
                )
                mismatch = client.post(
                    '/api/parser/cookies',
                    json={
                        'platform': 'xhs',
                        'cookies': '# Netscape HTTP Cookie File\n'
                        '.example.com\tTRUE\t/\tFALSE\t0\tweb_session\tnope\n',
                    },
                    headers=headers,
                )
                self.assertEqual(mismatch.status_code, 400)
                self.assertIn('域名', mismatch.json()['detail'])
                expired = client.post(
                    '/api/parser/cookies',
                    json={
                        'platform': 'xhs',
                        'cookies': '# Netscape HTTP Cookie File\n'
                        '.xiaohongshu.com\tTRUE\t/\tFALSE\t1\tweb_session\told\n',
                    },
                    headers=headers,
                )
                self.assertEqual(expired.status_code, 400)
                self.assertIn('过期', expired.json()['detail'])
                self.assertFalse(config.exists())

                response = client.post('/api/parser/cookies', json=payload, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data['count'], 2)
                self.assertEqual(data['platform'], 'xhs')
                self.assertTrue(data['secrets']['xhs.cookies'])
                self.assertTrue(data['file_written'])
                self.assertNotIn('secret-xhs', response.text)
                self.assertNotIn('abc', response.text)
                target = Path(tmp) / 'cookies' / 'xhs_cookies.txt'
                self.assertTrue(target.exists())
                content = target.read_text(encoding='utf-8')
                self.assertIn('#HttpOnly_.xiaohongshu.com', content)
                self.assertIn('\tweb_session\tsecret-xhs', content)
                self.assertEqual(
                    client.get('/api/parser').json()['secrets']['xhs.cookies'], True
                )

    def test_bilibili_netscape_import_writes_cookie_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            raw = (
                '# Netscape HTTP Cookie File\n'
                '#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsess\n'
                '.bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\tjct\n'
            )
            with patch.object(parser_settings, 'PATH', config), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'), \
                 patch.object(parser_credentials, 'write_bilibili_credential') as write:
                client, headers = self._login(tmp)
                response = client.post(
                    '/api/parser/bilibili/cookies',
                    json={'cookies': raw},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['cookie_file_written'])
                target = Path(tmp) / 'cookies' / 'bilibili_cookies.txt'
                self.assertTrue(target.exists())
                self.assertIn('\tSESSDATA\tsess', target.read_text(encoding='utf-8'))
                write.assert_called_once()

    def test_qr_endpoints_are_authenticated_and_csrf_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(d, 'session_store_path', lambda: Path(tmp) / 'sessions.json'), \
                 patch.object(
                     parser_credentials,
                     'create_bilibili_qr',
                     AsyncMock(return_value=b'\x89PNG\r\n'),
                 ), \
                 patch.object(
                     parser_credentials,
                     'poll_bilibili_qr',
                     AsyncMock(return_value={'status': 'waiting', 'message': '等待扫码'}),
                 ):
                self.assertEqual(
                    TestClient(d.app).post('/api/parser/bilibili/qr').status_code,
                    401,
                )
                client, headers = self._login(tmp)
                self.assertEqual(
                    client.post('/api/parser/bilibili/qr').status_code, 403
                )
                qr = client.post('/api/parser/bilibili/qr', headers=headers)
                self.assertEqual(qr.status_code, 200)
                self.assertEqual(qr.headers['content-type'], 'image/png')
                self.assertEqual(qr.content, b'\x89PNG\r\n')
                self.assertEqual(
                    client.post('/api/parser/bilibili/qr/status').status_code, 403
                )
                status = client.post('/api/parser/bilibili/qr/status', headers=headers)
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()['status'], 'waiting')


if __name__ == '__main__':
    unittest.main()
