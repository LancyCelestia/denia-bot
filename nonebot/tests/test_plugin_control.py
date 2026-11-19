"""插件开关的持久化、过滤和面板 API。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plugin_control


class PluginControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'plugins.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_and_roundtrip(self):
        self.assertEqual(plugin_control.load(self.path), {'onboarded': False, 'disabled': []})
        saved = plugin_control.save(['gsuid', 'bilibili'], onboarded=True, path=self.path)
        self.assertTrue(saved['onboarded'])
        self.assertTrue(saved['changed'])
        self.assertTrue(saved['restart_required'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['disabled'], ['bilibili', 'gsuid'])
        self.assertEqual(plugin_control.load(self.path)['disabled'], ['bilibili', 'gsuid'])

    def test_core_and_unknown_plugins_are_rejected(self):
        for plugin_id in ('gate', 'superusers', 'master_aware'):
            with self.assertRaises(ValueError):
                plugin_control.save([plugin_id], path=self.path)
        with self.assertRaises(ValueError):
            plugin_control.save(['not-a-plugin'], path=self.path)
        self.assertFalse(self.path.exists())
        view = plugin_control.view(self.path)
        locked = {item['id'] for item in view['plugins'] if item['core']}
        self.assertTrue({'superusers', 'master_aware'} <= locked)

    def test_module_normalization_and_core_are_enabled(self):
        self.assertEqual(plugin_control.normalize_target('./plugins/statuscard.py'), 'statuscard')
        self.assertEqual(plugin_control.normalize_target('GenshinUID'), 'gsuid')
        self.assertTrue(plugin_control.is_enabled('muicebot', {'bilibili'}))
        self.assertTrue(plugin_control.is_enabled('plugins.gate', {'gate'}))
        self.assertFalse(plugin_control.is_enabled('plugins.statuscard', {'statuscard'}))
        self.assertFalse(plugin_control.is_enabled('GenshinUID', {'gsuid'}))

    def test_startup_filter_skips_disabled_module(self):
        import nonebot
        plugin_control.save(['statuscard'], path=self.path)
        original = nonebot.load_plugin
        try:
            with patch.object(nonebot, 'load_plugin', return_value='loaded'):
                plugin_control.install_filter(self.path)
                self.assertIsNone(nonebot.load_plugin('plugins.statuscard'))
                self.assertEqual(nonebot.load_plugin('plugins.meme'), 'loaded')
        finally:
            nonebot.load_plugin = original
            if hasattr(nonebot, '_qqbot_plugin_filter'):
                delattr(nonebot, '_qqbot_plugin_filter')


if __name__ == '__main__':
    unittest.main()
