import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import dashboard as d
import memory_logic as memory
import group_topics_logic as topics
import weather_service as weather
import master_settings
import parser_settings
import bot_identity
from unittest.mock import AsyncMock, Mock, patch

class DashboardTest(unittest.TestCase):
    def test_parser_arbitration_setting_is_authorized_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                self.assertTrue(data['general']['arbiter'])
                update = {'version':data['version'], 'general':{'arbiter':False}}
                self.assertEqual(client.post('/api/parser', json=update).status_code, 403)
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                self.assertFalse(parser_settings.load()['arbiter'])
                self.assertFalse(client.get('/api/parser').json()['general']['arbiter'])

    def test_failed_restart_unlocks_only_after_new_process_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            controller=SimpleNamespace(running=True,error='failed')
            def ready(): controller.running=False; controller.error=''
            controller.observe_ready=Mock(side_effect=ready)
            monitor=SimpleNamespace(status=AsyncMock(return_value={'bot':{},'snowluma':{},'rag':{}}), bot_health=AsyncMock(return_value={'state':'ready','pid':1,'start_token':'old'}))
            worker=SimpleNamespace(available=True,expected_token='new',previous_pid=1,previous_state='ready')
            with patch.object(d,'monitor',monitor), patch.object(d,'restart_worker',worker), patch.object(d,'restart_controller',controller), TestClient(d.app) as client:
                client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertTrue(client.get('/api/bot-status').json()['restart']['running'])
                monitor.bot_health.return_value={'state':'ready','pid':2,'start_token':None}
                self.assertFalse(client.get('/api/bot-status').json()['restart']['running'])
    def test_restart_requires_login_and_csrf_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/bot-status').status_code,401)
                login=client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertEqual(client.post('/api/bot-restart').status_code,403)
                headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                with patch.object(d.restart_controller,'start',new_callable=AsyncMock,return_value=False):
                    self.assertEqual(client.post('/api/bot-restart',headers=headers).status_code,409)
    def test_bot_name_settings_require_login_csrf_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp) / 'auth'
            d.set_password('a-strong-test-password')
            target = Path(tmp) / 'bot-name.json'
            with patch.object(bot_identity, 'PATH', target), \
                    patch.object(bot_identity, 'get_private', lambda key: ''):
                with TestClient(d.app) as client:
                    self.assertEqual(client.get('/api/bot-name').status_code, 401)
                    login = client.post('/api/login', json={'password': 'a-strong-test-password'})
                    headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                    self.assertEqual(client.get('/api/bot-name').json()['name'], '达妮娅')
                    self.assertEqual(client.post('/api/bot-name', json={'name': '泡泡机'}).status_code, 403)
                    saved = client.post('/api/bot-name', json={'name': '泡泡机'}, headers=headers)
                    self.assertEqual(saved.status_code, 200)
                    self.assertEqual(saved.json()['source'], 'file')
                    self.assertEqual(client.get('/api/bot-name').json()['name'], '泡泡机')
                    self.assertTrue(target.exists())
                    self.assertEqual(client.post('/api/bot-name', json={'name': ''}, headers=headers).status_code, 400)
                    reset = client.post('/api/bot-name', json={'reset': True}, headers=headers)
                    self.assertEqual(reset.status_code, 200)
                    self.assertEqual(reset.json()['source'], 'default')
                    self.assertFalse(target.exists())

    def test_parser_settings_auth_validation_and_secret_preservation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/parser').status_code, 401)
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                self.assertIn('bilibili', data['platforms'])
                update = {'version':data['version'], 'general':{'debounce_interval':60}, 'platforms':{'bilibili':{'cookies':'private-cookie', 'enable':True}}}
                self.assertEqual(client.post('/api/parser', json=update).status_code, 403)
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                data = client.get('/api/parser').json()
                self.assertNotIn('private-cookie', str(data))
                self.assertTrue(data['secrets']['bilibili.cookies'])
                update = {'version':data['version'], 'general':{'debounce_interval':30}, 'platforms':{'bilibili':{'cookies':''}}}
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                self.assertEqual(parser_settings.load()['parsers_template'][0]['cookies'], 'private-cookie')
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 409)
                data = client.get('/api/parser').json()
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'source_max_size':-1}}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'whitelist':['invalid']}}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'clean_cron':''}}, headers=headers).status_code, 200)
                data = client.get('/api/parser').json()
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'clear_secrets':['bilibili.cookies']}, headers=headers).status_code, 200)
                self.assertFalse(client.get('/api/parser').json()['secrets']['bilibili.cookies'])
    def test_parser_payload_exposes_baseline_and_platform_summary(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                total = len(data['platforms'])
                # 基线用于前端判断「改没改过」，必须覆盖每个平台
                self.assertEqual(set(data['baseline']), set(data['platforms']))
                self.assertIn('enable', data['baseline']['bilibili'])
                self.assertEqual(data['summary']['total'], total)
                self.assertEqual(data['summary']['enabled'], len(data['summary']['names']))
                # 关掉一个平台后，总览要跟着变
                target = data['summary']['names'][0]
                update = {'version':data['version'], 'platforms':{target:{'enable':False}}}
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                after = client.get('/api/parser').json()
                self.assertEqual(after['summary']['enabled'], data['summary']['enabled'] - 1)
                self.assertNotIn(target, after['summary']['names'])
                self.assertEqual(after['summary']['total'], total)
    def test_summary_counts_only_explicit_true(self):
        platforms = {'a':{'enable':True}, 'b':{'enable':False}, 'c':{}}
        result = parser_settings.summary(platforms, baseline={})
        self.assertEqual(result, {'total':3, 'enabled':1, 'names':['a']})
    def test_master_settings_require_login_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(master_settings,'PATH',Path(tmp)/'settings.json'):
            d.AUTH_FILE=Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            data={'enabled':True,'owner_ids':['123456'],'title':'漂泊者'}
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/master-aware').status_code,401)
                login=client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertEqual(client.post('/api/master-aware',json=data).status_code,403)
                headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                self.assertEqual(client.post('/api/master-aware',json=data,headers=headers).status_code,200)
                self.assertEqual(client.get('/api/master-aware').json(),data)
                self.assertEqual(client.post('/api/master-aware',json={'enabled':True,'owner_ids':['x'],'title':None},headers=headers).status_code,400)
    def test_temporary_password_requires_change_and_revokes_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('temporary-pass-123', must_change=True)
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'temporary-pass-123'})
                self.assertTrue(login.json()['must_change'])
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                self.assertEqual(client.get('/api/state').status_code, 403)
                self.assertEqual(client.post('/api/password', json={'password':'too-short'}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/password', json={'password':'temporary-pass-123'}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/password', json={'password':'new-secret-pass-456'}, headers=headers).status_code, 200)
                self.assertEqual(client.get('/api/state').status_code, 401)
                self.assertEqual(client.post('/api/login', json={'password':'temporary-pass-123'}).status_code, 401)
                self.assertFalse(client.post('/api/login', json={'password':'new-secret-pass-456'}).json()['must_change'])

    def test_login_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            memory.DEFAULT_PATH = Path(tmp)/'memory.db'
            topics.DEFAULT_PATH = Path(tmp)/'topics.db'
            weather.PATH = Path(tmp)/'weather.db'
            # Functions bind their default path at definition time; patch only
            # the explicit paths used by memory/topic operations in this test.
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/state').status_code, 401)
                self.assertEqual(client.post('/api/login', json={'password':'wrong'}).status_code, 401)
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.post('/api/topic', json={'group':'123','action':'开'}).status_code, 403)
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                self.assertEqual(client.post('/api/topic', json={'group':'123','action':'开'}, headers=headers).status_code, 200)
                self.assertTrue(topics.enabled(topics.DEFAULT_PATH,'123'))
                self.assertEqual(client.get('/api/group/123').json()['enabled'], True)
                topics.record(topics.DEFAULT_PATH, '123', '1', '你好', user_id='42', display_name='群友', tokenizer=lambda _: [])
                ranking = client.get('/api/group/123').json()['speakers']
                self.assertEqual([(s['user_id'], s['display_name'], s['count']) for s in ranking], [('42', '群友', 1)])

    def test_status_grafana_token_is_stored_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d.AUTH_FILE = root/'auth'
            d.set_password('a-strong-test-password')
            config_path = root/'statuscard.json'
            with patch.object(d, 'status_paths', return_value=(config_path, root/'state.json', root/'status.db')), \
                    patch.object(d, 'ROOT', root), TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                response = client.post('/api/status/config', headers=headers, json={'grafana': {
                    'enabled': True, 'url': 'http://grafana:3000', 'datasource_uid': 'prom-uid',
                    'temp_query': 'max(node_hwmon_temp_celsius)', 'avg_window_minutes': 30,
                    'token': 'glsa_secret',
                }})
                self.assertEqual(response.status_code, 200)
                saved = json.loads(config_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['grafana']['datasource_uid'], 'prom-uid')
                self.assertNotIn('token', saved['grafana'])
                secret = json.loads((root/'data'/'status_grafana_secrets.json').read_text(encoding='utf-8'))
                self.assertEqual(secret['token'], 'glsa_secret')
                self.assertNotIn('glsa_secret', json.dumps(response.json()))

if __name__ == '__main__': unittest.main()
