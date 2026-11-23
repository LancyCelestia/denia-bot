"""Regression tests for the Xiaohongshu browser identity."""

from __future__ import annotations

import asyncio
import base64
import sys
import types
import unittest
from unittest.mock import patch

import xhs_login

VALID_QR = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A'
    'AwMCAO+/p9sAAAAASUVORK5CYII='
)


class _FakeQR:
    async def wait_for(self, **kwargs):
        return None

    async def screenshot(self, **kwargs):
        return base64.b64decode(VALID_QR)


class _FakePage:
    def set_default_timeout(self, timeout):
        return None

    def on(self, event, handler):
        return None

    async def goto(self, url, **kwargs):
        return None

    def locator(self, selector):
        return _FakeQR()

    async def wait_for_function(self, script, **kwargs):
        return None


class _FakeContext:
    async def route(self, pattern, handler):
        return None

    async def new_page(self):
        return _FakePage()

    async def cookies(self, url):
        return []

    async def close(self):
        return None


class _FakeBrowser:
    version = '148.0.7778.96'

    def __init__(self):
        self.context_kwargs = {}

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return _FakeContext()

    async def close(self):
        return None


class _FakePlaywright:
    def __init__(self):
        self.browser = _FakeBrowser()
        browser = self.browser

        class Chromium:
            async def launch(self, **kwargs):
                return browser

        self.chromium = Chromium()

    async def stop(self):
        return None


def _fake_async_playwright():
    class Manager:
        async def start(self):
            return _FakePlaywright()

    return Manager()


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

    def test_browser_context_receives_desktop_chrome_user_agent(self):
        api = types.ModuleType('playwright.async_api')
        api.async_playwright = _fake_async_playwright
        package = types.ModuleType('playwright')
        package.async_api = api
        browser = xhs_login.XhsBrowser()

        with patch.dict(
            sys.modules,
            {'playwright': package, 'playwright.async_api': api},
        ):
            qr = asyncio.run(browser.open())

        self.assertTrue(qr)
        captured = browser._browser.context_kwargs['user_agent']
        self.assertNotIn('HeadlessChrome', captured)
        self.assertIn('Chrome/148.0.0.0', captured)


if __name__ == '__main__':
    unittest.main()
