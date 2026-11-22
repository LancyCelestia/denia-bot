"""Continuation-page labels: templates only declare data-page-label; the renderer inserts the header (see docker/playwright-renderer)."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import card_document
import group_topics_image
import status_image
import weather_image
from media_parser.core.data import Author, ParseResult, Platform
from media_parser.core.render import Renderer
from tests.test_status_image import view


class PageLabelTest(unittest.TestCase):
    def test_document_declares_and_escapes_the_label(self):
        html = card_document.document(['<p>x</p>'], width=760, page_label='A & <B>')
        self.assertIn('<main data-card data-page-label="A &amp; &lt;B&gt;" class="sheet ', html)
        self.assertNotIn('data-page-label', card_document.document(['<p>x</p>'], width=760))
        self.assertIn('[data-render-continued]', html)

    def test_cards_carry_a_specific_label(self):
        forecast = {'kind': 'forecast', 'place': '杭州', 'days': [
            {'label': '今天', 'short': '11/22', 'condition': '晴', 'min': 1, 'max': 9}]}
        self.assertIn('天气预报"', weather_image.build_html(forecast))
        self.assertIn('群话题"', group_topics_image.build_html('1', [('词', 3)]))
        self.assertIn(' · 服务器状态"', status_image.build_status_html(view()))
        report = status_image.build_report_html({'title': '服务器周报', 'sections': []})
        self.assertIn(' · 服务器周报"', report)

    def test_parser_label_names_platform_and_author(self):
        result = ParseResult(platform=Platform('weibo', '微博'), author=Author('某人'), text='x')
        self.assertEqual(Renderer.page_label(result), '微博 · 某人')
        self.assertEqual(Renderer.page_label(ParseResult(platform=Platform('x', 'X'))), 'X')
        html = asyncio.run(Renderer(None).build_html(result))
        self.assertIn('data-page-label="微博 · 某人"', html)


if __name__ == '__main__':
    unittest.main()
