"""Fixed gsuid_core service control without touching the real service."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_services


class ExternalServiceTest(unittest.TestCase):
    def test_only_gsuid_is_mapped(self):
        self.assertIsNone(external_services.apply_plugin_change('bilibili', False))

    def test_systemd_stop_and_start(self):
        with patch.object(external_services, '_systemd_unit_exists', return_value=True), \
                patch.object(external_services, '_run', return_value=Mock(returncode=0)) as run:
            stopped = external_services.stop_gsuid(platform='linux')
            started = external_services.start_gsuid(platform='linux')
        self.assertEqual(stopped['backend'], 'systemd')
        self.assertFalse(stopped['running'])
        self.assertTrue(started['running'])
        self.assertEqual(run.call_args_list[0].args[0], ['systemctl', 'disable', '--now', 'qqbot-gsuid-core.service'])
        self.assertEqual(run.call_args_list[1].args[0], ['systemctl', 'enable', '--now', 'qqbot-gsuid-core.service'])

    def test_docker_fallback(self):
        with patch.object(external_services, '_systemd_unit_exists', return_value=False), \
                patch.object(external_services, '_docker_container_exists', return_value=True), \
                patch.object(external_services, '_docker_running', return_value=True), \
                patch.object(external_services, '_run', return_value=Mock(returncode=0)) as run:
            result = external_services.stop_gsuid(platform='linux')
        self.assertEqual(result['backend'], 'docker')
        run.assert_called_once_with(['docker', 'stop', 'gsuid_core'], timeout=60)

    def test_gsuid_without_service_is_reported(self):
        with patch.object(external_services, '_systemd_unit_exists', return_value=False), \
                patch.object(external_services, '_docker_container_exists', return_value=False):
            result = external_services.stop_gsuid(platform='linux')
        self.assertFalse(result['managed'])
        self.assertIn('未发现', result['warning'])


if __name__ == '__main__':
    unittest.main()
