import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from media_parser.core.config import PluginConfig


class ParserConfigTests(unittest.TestCase):
    def test_concurrent_session_changes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            configs = [PluginConfig(path) for _ in range(8)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda item: item[1].add_blacklist(f'group:{item[0]+1}'), enumerate(configs)))
            self.assertEqual(set(PluginConfig(path).blacklist), {f'group:{i}' for i in range(1, 9)})

    def test_session_switch_does_not_overwrite_dashboard_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            bot = PluginConfig(path)
            dashboard = PluginConfig(path)
            dashboard.debounce_interval = 41
            dashboard.save_config()
            bot.add_blacklist('group:123')
            result = PluginConfig(path)
            self.assertEqual(result.debounce_interval, 41)
            self.assertIn('group:123', result.blacklist)

    def test_default_platforms_and_persisted_blacklist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            config = PluginConfig(path)
            self.assertIn('bilibili', config.parser.enabled_platforms())
            self.assertNotIn('jmcomic', config.parser.enabled_platforms())
            self.assertIn('youtube', config.parser.platforms())
            self.assertNotIn('youtube', config.parser.enabled_platforms())
            config.add_blacklist('group:123')
            self.assertIn('group:123', PluginConfig(path).blacklist)

    def test_saved_platform_list_is_backfilled_without_enabling_new_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps({'parsers_template': [
                {'__template_key': 'bilibili', 'enable': True, 'cookies': 'private-cookie'},
                {'__template_key': 'legacy_custom', 'enable': True, 'foo': 'bar'},
            ]}), encoding='utf-8')
            config = PluginConfig(path)
            entries = {entry['__template_key']: entry for entry in config.parsers_template}
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / 'media_parser' / '_conf_schema.json').read_text(encoding='utf-8')
            )
            expected = set(schema['parsers_template']['templates'])
            self.assertEqual(set(entries) - {'legacy_custom'}, expected)
            self.assertEqual(entries['bilibili']['cookies'], 'private-cookie')
            self.assertIs(entries['bilibili']['enable'], True)
            self.assertIs(entries['xhs']['enable'], False)
            self.assertEqual(entries['legacy_custom']['foo'], 'bar')
            self.assertEqual(
                config.parser.platforms(),
                [entry['__template_key'] for entry in config.parsers_template],
            )


if __name__ == '__main__':
    unittest.main()
