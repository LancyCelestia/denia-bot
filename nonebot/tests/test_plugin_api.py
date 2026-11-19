"""插件管理接口的认证、CSRF 与持久化。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import dashboard as d
import plugin_control


class PluginApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'plugins.json'
        self.patch_path = patch.object(plugin_control, 'DEFAULT_PATH', self.path)
        self.patch_path.start()
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
        self.patch_path.stop()
        self.temp.cleanup()

    def test_plugins_require_login_and_csrf(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/plugins').status_code, 401)
        self.assertEqual(self.client.post('/api/plugins', json={'disabled': []}).status_code, 401)

    def test_save_persists_and_marks_onboarded(self):
        self.assertEqual(self.client.get('/api/plugins').status_code, 200)
        self.assertEqual(
            self.client.post('/api/plugins', json={'disabled': [], 'onboarded': False}).status_code,
            403,
        )
        with patch.object(
            d.external_services, 'apply_plugin_change',
            return_value={'service': 'gsuid_core', 'managed': True,
                          'backend': 'test', 'running': False},
        ) as service:
            response = self.client.post(
                '/api/plugins',
                json={'disabled': ['gsuid', 'bilibili'], 'onboarded': True},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        service.assert_called_once_with('gsuid', False)
        data = response.json()
        self.assertTrue(data['onboarded'])
        self.assertTrue(data['changed'])
        self.assertEqual(data['disabled'], ['bilibili', 'gsuid'])
        self.assertEqual(data['service']['backend'], 'test')
        self.assertTrue(self.path.exists())
        view = self.client.get('/api/plugins').json()
        self.assertTrue(view['onboarded'])
        self.assertEqual(view['disabled'], ['bilibili', 'gsuid'])

    def test_protected_plugins_are_rejected(self):
        response = self.client.post(
            '/api/plugins',
            json={'disabled': ['gate'], 'onboarded': True},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('核心插件', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
