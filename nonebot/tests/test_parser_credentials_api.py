"""Dashboard API tests for Bilibili credential import and QR login."""

import sys
import tempfile
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
