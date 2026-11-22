"""Parser preview card layout: quote boxes, grids, text chunks. Pure HTML, no renderer needed."""
import asyncio
import base64
import re
import sys
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image

from card_document import image_uri, split_text
from media_parser.core.data import Author, ImageContent, ParseResult, Platform
from media_parser.core.render import Renderer


def picture(folder, name, size):
    path = Path(folder) / f'{name}.jpg'
    Image.new('RGB', size, (90, 60, 120)).save(path, 'JPEG')
    return path


def natural_sizes(html):
    sizes = []
    for uri in re.findall(r'<img src="data:image/jpeg;base64,([^"]+)"', html):
        with Image.open(BytesIO(base64.b64decode(uri))) as image:
            sizes.append(image.size)
    return sizes


class SplitTextTest(unittest.TestCase):
    def test_prefers_paragraph_then_sentence_boundaries(self):
        text = '第一句。' * 60 + '\n\n' + '第二段很长，' * 80
        chunks = split_text(text, 300)
        self.assertTrue(all(len(chunk) <= 300 for chunk in chunks))
        self.assertEqual(''.join(chunks).replace('\n', ''), text.replace('\n', ''))
        self.assertTrue(chunks[0].endswith('。'))
        self.assertTrue(all(chunk.endswith(('。', '，')) for chunk in chunks[:-1]))

    def test_hard_cut_only_without_punctuation(self):
        self.assertEqual([len(c) for c in split_text('字' * 650, 300)], [300, 300, 50])
        self.assertEqual(split_text('', 300), [])
        self.assertEqual(split_text('短', 300), ['短'])


class ImageCropTest(unittest.TestCase):
    def test_crop_fills_the_cell_without_upscaling(self):
        with TemporaryDirectory() as folder:
            wide = picture(folder, 'wide', (1920, 1080))
            small = picture(folder, 'small', (200, 120))
            for path, expected in ((wide, (368, 368)), (small, (120, 120))):
                uri = image_uri(path, width=368, height=368, crop=True)
                with Image.open(BytesIO(base64.b64decode(uri.split(',', 1)[1]))) as image:
                    self.assertEqual(image.size, expected)
            # 默认行为不变：等比缩进框内
            uri = image_uri(wide, width=350, height=350)
            with Image.open(BytesIO(base64.b64decode(uri.split(',', 1)[1]))) as image:
                self.assertEqual(image.size, (350, 197))


class ParserCardTest(unittest.IsolatedAsyncioTestCase):
    async def build(self, result):
        return await Renderer(None).build_html(result)

    async def test_grid_is_one_block_with_cells_at_display_size(self):
        with TemporaryDirectory() as folder:
            images = [ImageContent(picture(folder, f'p{i}', (1920, 1080))) for i in range(9)]
            html = await self.build(ParseResult(platform=Platform('xhs', '小红书'), contents=images))
        self.assertEqual(html.count('class="grid"'), 1)
        self.assertEqual(set(natural_sizes(html)), {(243, 243)})   # (744 - 8*2) / 3
        self.assertIn('.grid{display:grid;gap:8px}', html)

    async def test_two_images_use_two_columns(self):
        with TemporaryDirectory() as folder:
            images = [ImageContent(picture(folder, f'p{i}', (1080, 1440))) for i in range(2)]
            html = await self.build(ParseResult(platform=Platform('twitter', 'X'), contents=images))
        self.assertIn('repeat(2,1fr)', html)
        self.assertEqual(set(natural_sizes(html)), {(368, 368)})

    async def test_reposts_form_one_continuous_quote_with_depth_bars(self):
        inner = ParseResult(platform=Platform('weibo', '微博'), author=Author('B'), text='原文')
        middle = ParseResult(platform=Platform('weibo', '微博'), author=Author('A'), text='转发理由', repost=inner)
        outer = ParseResult(platform=Platform('weibo', '微博'), author=Author('作者'), text='正文', repost=middle)
        blocks = await Renderer(None)._blocks(outer)
        reposts = [block for block in blocks if block.startswith('<section class="repost')]
        self.assertEqual(len(reposts), 4)                # A 的页眉+正文，B 的页眉+正文
        self.assertIn('class="repost first"', reposts[0])
        self.assertIn('class="repost last"', reposts[-1])
        self.assertTrue(all('class="repost first' not in block for block in reposts[1:]))
        self.assertEqual(reposts[0].count('#d7cec3'), 1)  # 第一层一条竖条
        self.assertEqual(reposts[-1].count('#d7cec3'), 2)  # 第二层两条
        self.assertTrue(all(block.count('<section') == 1 for block in reposts))  # 不再层层嵌套
        self.assertEqual(sum(block.count('转发内容') for block in reposts), 2)
        html = await self.build(outer)
        self.assertEqual(html.count('<header'), 3)

    async def test_repost_depth_is_bounded(self):
        result = ParseResult(platform=Platform('weibo', '微博'), text='0')
        node = result
        for depth in range(1, 8):
            node.repost = ParseResult(platform=Platform('weibo', '微博'), text=str(depth))
            node = node.repost
        html = await self.build(result)
        self.assertEqual(html.count('<header'), Renderer.MAX_DEPTH + 1)
        self.assertIn('更早的转发请查看原帖', html)

    async def test_long_text_splits_at_sentence_ends(self):
        text = '这一句话说完了。' * 120
        html = await self.build(ParseResult(platform=Platform('weibo', '微博'), text=text))
        chunks = re.findall(r'<div class="text">([^<]*)</div>', html)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.endswith('。') for chunk in chunks))

    async def test_secondary_text_meets_contrast(self):
        html = await self.build(ParseResult(platform=Platform('unknown', '某平台'), text='x'))
        self.assertNotIn('#8b8175', html)
        self.assertIn('.muted{color:#6b645a}', html)


if __name__ == '__main__':
    unittest.main()
