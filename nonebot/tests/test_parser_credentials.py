"""Bilibili parser credential import and no-restart reload tests."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parser_credentials
import parser_settings
from media_parser.core.cookie import CookieJar

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


class NetscapeCookieTest(unittest.TestCase):
    @staticmethod
    def _file(expires: int = 0, domain: str = '.bilibili.com') -> str:
        return (
            '# Netscape HTTP Cookie File\n'
            '# https://curl.haxx.se/docs/http-cookies.html\n'
            f'{domain}\tTRUE\t/\tFALSE\t{expires}\tSESSDATA\tsess-value\n'
            f'{domain}\tTRUE\t/\tTRUE\t{expires}\tbili_jct\tjct-value\n'
            f'{domain}\tTRUE\t/\tFALSE\t{expires}\tDedeUserID\t123456\n'
        )

    def test_normal_netscape_file(self):
        cookies = parser_credentials.parse_cookie_input(self._file())
        self.assertEqual(cookies['SESSDATA'], 'sess-value')
        self.assertEqual(cookies['bili_jct'], 'jct-value')
        self.assertEqual(cookies['DedeUserID'], '123456')

    def test_httponly_prefix_is_preserved(self):
        raw = (
            '# Netscape HTTP Cookie File\n'
            '#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsess-value\n'
            '.bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\tjct-value\n'
        )
        cookies = parser_credentials.parse_cookie_input(raw)
        self.assertEqual(cookies['SESSDATA'], 'sess-value')
        self.assertEqual(cookies['bili_jct'], 'jct-value')

    def test_expired_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '过期'):
            parser_credentials.parse_cookie_input(self._file(expires=1))

    def test_cross_domain_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '域名'):
            parser_credentials.parse_cookie_input(self._file(domain='.example.com'))

    def test_crlf_file_is_supported(self):
        cookies = parser_credentials.parse_cookie_input(
            self._file().replace('\n', '\r\n')
        )
        self.assertEqual(cookies['bili_jct'], 'jct-value')

    def test_oversized_file_is_rejected(self):
        raw = (
            '# Netscape HTTP Cookie File\n'
            '.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t'
            + 'x' * 33000
            + '\n'
        )
        with self.assertRaisesRegex(ValueError, '32 KiB'):
            parser_credentials.parse_cookie_input(raw)


class NetscapeCookieFileTest(unittest.TestCase):
    def test_domain_map_covers_every_platform_cookies_field(self):
        templates = parser_settings.schema()['parsers_template']['templates']
        with_cookies = {
            name
            for name, spec in templates.items()
            if 'cookies' in (spec.get('items') or {})
        }
        self.assertEqual(with_cookies - set(parser_credentials.PLATFORM_DOMAINS), set())

    def test_bilibili_config_is_readable_by_cookiejar(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parser_settings, 'PATH', Path(tmp) / 'config.json'
        ):
            parser_credentials.save_bilibili_config(
                {'SESSDATA': 'sess', 'bili_jct': 'jct', 'DedeUserID': '123'}
            )
            entries = {
                item['__template_key']: item
                for item in parser_settings.load()['parsers_template']
            }
            cookie_dir = Path(tmp) / 'cookies'
            cookie_dir.mkdir()
            cfg = SimpleNamespace(cookie_dir=cookie_dir)
            jar = CookieJar(
                cfg,
                SimpleNamespace(name='bilibili', cookies=entries['bilibili']['cookies']),
                'bilibili.com',
            )
            self.assertEqual(jar.get()['SESSDATA'], 'sess')
            self.assertIn('SESSDATA=sess', jar.get_cookie_header())
            self.assertIn('bili_jct=jct', jar.get_cookie_header())

            reloaded = CookieJar(
                cfg,
                SimpleNamespace(name='bilibili', cookies=''),
                'bilibili.com',
            )
            self.assertEqual(reloaded.get()['SESSDATA'], 'sess')

    def test_bilibili_import_file_is_readable_by_cookiejar(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parser_settings, 'PATH', Path(tmp) / 'config.json'
        ), patch.object(parser_credentials, 'write_bilibili_credential'):
            parser_credentials.save_bilibili_cookies(
                {'SESSDATA': 'sess', 'bili_jct': 'jct', 'DedeUserID': '123'}
            )
            cookie_dir = Path(tmp) / 'cookies'
            jar = CookieJar(
                SimpleNamespace(cookie_dir=cookie_dir),
                SimpleNamespace(name='bilibili', cookies=''),
                'bilibili.com',
            )
            self.assertEqual(jar.get()['SESSDATA'], 'sess')
            self.assertIn('bili_jct=jct', jar.get_cookie_header())

    def test_generic_import_writes_filtered_atomic_file(self):
        far = int(time.time()) + 86400
        raw = (
            '# Netscape HTTP Cookie File\n'
            f'#HttpOnly_.xiaohongshu.com\tTRUE\t/\tTRUE\t{far}\tweb_session\tsecret-xhs\n'
            '.xiaohongshu.com\tTRUE\t/\tFALSE\t0\ta1\tabc\n'
            f'.example.com\tTRUE\t/\tFALSE\t{far}\tforeign\tzzz\n'
            '.xiaohongshu.com\tTRUE\t/\tFALSE\t1\texpired\told\n'
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parser_settings, 'PATH', Path(tmp) / 'config.json'
        ):
            result = parser_credentials.save_platform_cookies('xhs', raw)
            self.assertEqual(result['count'], 2)
            self.assertTrue(result['secrets']['xhs.cookies'])
            self.assertNotIn('secret-xhs', result['message'])

            entries = {
                item['__template_key']: item
                for item in parser_settings.load()['parsers_template']
            }
            self.assertEqual(
                entries['xhs']['cookies'], 'web_session=secret-xhs; a1=abc'
            )
            target = Path(tmp) / 'cookies' / 'xhs_cookies.txt'
            self.assertTrue(target.exists())
            content = target.read_text(encoding='utf-8')
            self.assertIn('#HttpOnly_.xiaohongshu.com', content)
            self.assertIn('\tweb_session\tsecret-xhs', content)
            self.assertNotIn('expired', content)
            self.assertNotIn('example.com', content)
            if os.name != 'nt':
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_cookiejar_reloads_file_by_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_dir = Path(tmp) / 'cookies'
            cookie_dir.mkdir()
            target = cookie_dir / 'bilibili_cookies.txt'
            target.write_text(
                '# Netscape HTTP Cookie File\n'
                '.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\told\n',
                encoding='utf-8',
            )
            cfg = SimpleNamespace(cookie_dir=cookie_dir)
            jar = CookieJar(
                cfg, SimpleNamespace(name='bilibili', cookies=''), 'bilibili.com'
            )
            self.assertEqual(jar.get()['SESSDATA'], 'old')

            target.write_text(
                '# Netscape HTTP Cookie File\n'
                '.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tnew\n',
                encoding='utf-8',
            )
            newer = time.time() + 2
            os.utime(target, (newer, newer))
            self.assertEqual(jar.get()['SESSDATA'], 'new')

    def test_base_parser_refreshes_cached_cookie_headers(self):
        from media_parser.core.parsers.base import BaseParser

        with tempfile.TemporaryDirectory() as tmp:
            cookie_dir = Path(tmp) / 'cookies'
            cookie_dir.mkdir()
            target = cookie_dir / 'xhs_cookies.txt'
            target.write_text(
                '# Netscape HTTP Cookie File\n'
                '.xiaohongshu.com\tTRUE\t/\tFALSE\t0\tweb_session\told\n',
                encoding='utf-8',
            )
            jar = CookieJar(
                SimpleNamespace(cookie_dir=cookie_dir),
                SimpleNamespace(name='xhs', cookies=''),
                'xiaohongshu.com',
            )
            parser = SimpleNamespace(
                cookiejar=jar,
                headers={'cookie': 'web_session=old'},
                ios_headers={},
            )
            BaseParser.refresh_cookies(parser)
            self.assertEqual(parser.headers['cookie'], 'web_session=old')

            target.write_text(
                '# Netscape HTTP Cookie File\n'
                '.xiaohongshu.com\tTRUE\t/\tFALSE\t0\tweb_session\tnew\n',
                encoding='utf-8',
            )
            newer = time.time() + 2
            os.utime(target, (newer, newer))
            BaseParser.refresh_cookies(parser)
            self.assertEqual(parser.headers['cookie'], 'web_session=new')
            self.assertEqual(parser.ios_headers['cookie'], 'web_session=new')


if __name__ == '__main__':
    unittest.main()
