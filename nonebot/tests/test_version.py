"""当前产品的展示版本与 Python 包版本。"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_version
import dashboard


class VersionTest(unittest.TestCase):
    def test_display_and_package_versions_are_defined(self):
        self.assertEqual(app_version.VERSION, 'V0.1 BETA2')
        self.assertEqual(app_version.PACKAGE_VERSION, '0.1.0b2')

    def test_dashboard_index_injects_display_version(self):
        text = dashboard.index()
        self.assertIn('V0.1 BETA2', text)
        self.assertNotIn('{{APP_VERSION}}', text)


if __name__ == '__main__':
    unittest.main()
