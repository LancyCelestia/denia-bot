"""Regression tests for the Xiaohongshu browser identity."""

from __future__ import annotations

import unittest

import xhs_login


class XhsUserAgentTest(unittest.TestCase):
    def test_uses_desktop_chrome_instead_of_headless_chrome(self):
        user_agent = xhs_login._chrome_user_agent('148.0.7778.96', 'linux')

        self.assertNotIn('HeadlessChrome', user_agent)
        self.assertIn('Chrome/148.0.0.0', user_agent)
        self.assertIn('X11; Linux x86_64', user_agent)

    def test_platform_matches_the_host(self):
        self.assertIn(
            'Windows NT 10.0; Win64; x64',
            xhs_login._chrome_user_agent('148.0.7778.96', 'win32'),
        )
        self.assertIn(
            'Macintosh; Intel Mac OS X 10_15_7',
            xhs_login._chrome_user_agent('148.0.7778.96', 'darwin'),
        )


if __name__ == '__main__':
    unittest.main()
