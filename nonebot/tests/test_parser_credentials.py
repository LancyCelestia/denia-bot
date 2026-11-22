"""Bilibili parser credential import and no-restart reload tests."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parser_credentials
import parser_settings

try:
    from media_parser.core.parsers.bilibili.login import BilibiliLogin
except Exception:  # optional parser dependencies are not part of requirements.lock
    BilibiliLogin = None


class CookieInputTest(unittest.TestCase):
    def test_cookie_header(self):
        cookies = parser_credentials.parse_cookie_input(
            'SESSDATA=sess; bili_jct=jct; DedeUserID=123456'
        )
        self.assertEqual(cookies['SESSDATA'], 'sess')
        self.assertEqual(cookies['bili_jct'], 'jct')
        self.assertEqual(cookies['DedeUserID'], '123456')

    def test_cookie_prefix_and_unknown_names_are_ignored(self):
        cookies = parser_credentials.parse_cookie_input(
            'Cookie: buvid3=abc; SESSDATA=sess; bili_jct=jct'
        )
        self.assertEqual(cookies, {'SESSDATA': 'sess', 'bili_jct': 'jct'})

    def test_json_object_and_browser_array(self):
        obj = parser_credentials.parse_cookie_input(
            '{"SESSDATA":"sess","bili_jct":"jct","DedeUserID":"123"}'
        )
        arr = parser_credentials.parse_cookie_input(
            '[{"name":"SESSDATA","value":"sess"},{"name":"bili_jct","value":"jct"}]'
        )
        self.assertEqual(obj['DedeUserID'], '123')
        self.assertEqual(arr, {'SESSDATA': 'sess', 'bili_jct': 'jct'})

    def test_missing_required_field_names_the_field(self):
        with self.assertRaisesRegex(ValueError, 'bili_jct'):
            parser_credentials.parse_cookie_input('SESSDATA=sess')
        with self.assertRaisesRegex(ValueError, 'SESSDATA'):
            parser_credentials.parse_cookie_input('{"bili_jct":"jct"}')

    def test_invalid_fragment_and_value(self):
        with self.assertRaisesRegex(ValueError, '缺少 ='):
            parser_credentials.parse_cookie_input('SESSDATA=sess; broken')
        with self.assertRaisesRegex(ValueError, '非法字符'):
            parser_credentials.parse_cookie_input('SESSDATA=sess; bili_jct=j,ct')
        with self.assertRaisesRegex(ValueError, 'DedeUserID'):
            parser_credentials.parse_cookie_input(
                'SESSDATA=sess; bili_jct=jct; DedeUserID=abc'
            )


class SaveConfigTest(unittest.TestCase):
    def test_save_updates_only_bilibili_secret_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parser_settings, 'PATH', Path(tmp) / 'config.json'
        ):
            initial = parser_settings.public()
            parser_settings.save(
                {
                    'version': initial['version'],
                    'general': {'arbiter': False},
                    'platforms': {},
                    'clear_secrets': [],
                }
            )
            result = parser_credentials.save_bilibili_config(
                {'SESSDATA': 'sess', 'bili_jct': 'jct', 'DedeUserID': '123'}
            )
            self.assertTrue(result['secrets']['bilibili.cookies'])
            loaded = parser_settings.load()
            entries = {item['__template_key']: item for item in loaded['parsers_template']}
            self.assertEqual(
                entries['bilibili']['cookies'],
                'SESSDATA=sess; bili_jct=jct; DedeUserID=123',
            )
            self.assertFalse(loaded['arbiter'])

    def test_invalid_input_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            with patch.object(parser_settings, 'PATH', path):
                with self.assertRaises(ValueError):
                    parser_credentials.parse_cookie_input('SESSDATA=sess')
                self.assertFalse(path.exists())


@unittest.skipIf(BilibiliLogin is None, 'optional Bilibili parser dependencies are unavailable')
class CredentialReloadTest(unittest.TestCase):
    def test_dashboard_written_credential_reloads_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(
                data_dir=Path(tmp) / 'data',
                config_path=Path(tmp) / 'config.json',
                parser=SimpleNamespace(bilibili=SimpleNamespace(cookies='')),
            )
            first = BilibiliLogin(cfg)
            first.save_cookies(
                {'SESSDATA': 'new-sess', 'bili_jct': 'jct', 'DedeUserID': '123'}
            )
            second = BilibiliLogin(cfg)
            second._credential = first._credential
            second._credential_mtime = None
            second._reload_if_changed()
            self.assertIsNotNone(second._credential)
            self.assertEqual(second._credential.get_cookies()['SESSDATA'], 'new-sess')
            if os.name != 'nt':
                mode = (Path(tmp) / 'data' / 'cookies' / 'bilibili_credential.json').stat().st_mode
                self.assertEqual(mode & 0o777, 0o600)

    def test_invalid_credential_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(
                data_dir=Path(tmp) / 'data',
                config_path=Path(tmp) / 'config.json',
                parser=SimpleNamespace(bilibili=SimpleNamespace(cookies='')),
            )
            path = Path(tmp) / 'data' / 'cookies' / 'bilibili_credential.json'
            path.parent.mkdir(parents=True)
            path.write_text('[]', encoding='utf-8')
            login = BilibiliLogin(cfg)
            login._load_credential()
            self.assertIsNone(login._credential)
            self.assertEqual(login.subscription_cookies(), {})

    def test_save_cookies_requires_sessdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(
                data_dir=Path(tmp) / 'data',
                config_path=Path(tmp) / 'config.json',
                parser=SimpleNamespace(bilibili=SimpleNamespace(cookies='')),
            )
            with self.assertRaisesRegex(ValueError, 'SESSDATA'):
                BilibiliLogin(cfg).save_cookies({'bili_jct': 'jct'})


if __name__ == '__main__':
    unittest.main()
