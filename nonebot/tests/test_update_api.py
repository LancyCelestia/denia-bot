"""面板更新接口的认证、CSRF 和重启前置条件。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import dashboard as d


class UpdateApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        d.AUTH_FILE = Path(self.temp.name) / 'auth'
        d.set_password('a-strong-test-password')
        self.client = TestClient(d.app)
        login = self.client.post('/api/login', json={'password': 'a-strong-test-password'})
        self.headers = {
            'Origin': 'http://testserver',
            'X-CSRF-Token': login.json()['csrf'],
        }

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_status_requires_login(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/update').status_code, 401)

    def test_check_returns_upstream_state(self):
        updater = SimpleNamespace(
            status=Mock(return_value={'available': True}),
            check=Mock(return_value={
                'available': True, 'behind': 1, 'ahead': 0,
                'can_apply': True, 'requirements_changed': False,
            }),
        )
        with patch.object(d, 'updater', updater):
            self.assertEqual(self.client.get('/api/update').status_code, 200)
            response = self.client.post(
                '/api/update', json={'action': 'check'}, headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['behind'], 1)

    def test_apply_requires_csrf(self):
        updater = SimpleNamespace(
            status=Mock(return_value={'available': True}),
            apply=Mock(return_value={'available': True, 'updated': True}),
        )
        with patch.object(d, 'updater', updater), \
                patch.object(d, 'restart_worker', SimpleNamespace(available=True)), \
                patch.object(d, 'restart_controller', SimpleNamespace(
                    running=False, start=AsyncMock(return_value=True))):
            self.assertEqual(
                self.client.post('/api/update', json={'action': 'apply'}).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    '/api/update', json={'action': 'apply'}, headers=self.headers
                ).status_code,
                202,
            )

    def test_apply_is_refused_without_restart_support(self):
        updater = SimpleNamespace(
            status=Mock(return_value={'available': True}),
            apply=Mock(return_value={'available': True, 'updated': True}),
        )
        with patch.object(d, 'updater', updater), \
                patch.object(d, 'restart_worker', SimpleNamespace(available=False)):
            response = self.client.post(
                '/api/update', json={'action': 'apply'}, headers=self.headers
            )
        self.assertEqual(response.status_code, 503)
        updater.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
