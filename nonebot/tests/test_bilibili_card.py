"""B站视频动态卡片：标题后应带压缩后的视频封面。"""
import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bilibili.client import normalize_item
from bilibili.delivery import OneBotSender
from bilibili.model import Item, Scope
from bilibili.render import render_card


def raw_video():
    return {
        'id_str': '1234567890123456789',
        'type': 'DYNAMIC_TYPE_AV',
        'visible': True,
        'modules': {
            'module_author': {
                'mid': 123,
                'name': '测试UP',
                'face': 'https://i0.hdslb.com/bfs/face/test.jpg',
                'pub_ts': 1700000000,
            },
            'module_dynamic': {
                'desc': {'text': '发布了视频'},
                'major': {
                    'type': 'MAJOR_TYPE_ARCHIVE',
                    'archive': {
                        'aid': '1',
                        'bvid': 'BV1xx411c7mD',
                        'title': '测试视频标题',
                        'desc': '视频简介',
                        'cover': 'http://i0.hdslb.com/bfs/archive/test.jpg',
                    },
                },
            },
        },
    }


def cover_bytes():
    image = Image.new('RGB', (640, 360), '#c0392b')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


class VideoDynamicCardTest(unittest.TestCase):
    def test_normalize_keeps_video_title_and_cover(self):
        item = normalize_item(raw_video(), '123')
        self.assertEqual(item.kind, '视频动态')
        self.assertEqual(item.title, '测试视频标题')
        self.assertEqual(item.cover, 'https://i0.hdslb.com/bfs/archive/test.jpg')
        self.assertIn('测试视频标题', item.text)

    def test_render_places_cover_after_title(self):
        item = normalize_item(raw_video(), '123')
        plain = Image.open(BytesIO(render_card(item)))
        with_cover = Image.open(BytesIO(render_card(item, cover=cover_bytes())))
        self.assertGreater(with_cover.height, plain.height)
        self.assertLessEqual(with_cover.height, 2600)

    def test_item_rejects_untrusted_cover_host(self):
        item = normalize_item(raw_video(), '123')
        untrusted = Item(
            item.id, item.uid, item.name, item.ts, item.kind, item.text,
            item.face, item.title, 'https://example.com/cover.jpg',
        )
        self.assertEqual(untrusted.cover, '')


class SenderCoverTest(unittest.IsolatedAsyncioTestCase):
    async def test_sender_fetches_and_forwards_video_cover(self):
        rendered = []

        class Bot:
            self_id = '1'

            async def call_api(self, *args, **kwargs):
                return {'message_id': '42'}

        async def cover(item):
            return b'cover-bytes'

        def render(item, *, avatar=None, cover=None):
            rendered.append(cover)
            return b'PNG'

        sender = OneBotSender(lambda: {'1': Bot()}, render=render, cover=cover)
        item = normalize_item(raw_video(), '123')
        receipt = await sender.send(Scope('1', 'private', '2'), item)
        self.assertEqual(rendered, [b'cover-bytes'])
        self.assertEqual(receipt.kind, 'sent')


if __name__ == '__main__':
    unittest.main()
