import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from plugins import meme_learn as plugin
import meme_learning
import meme_logic


def picture():
    out = io.BytesIO()
    Image.new('RGB', (3, 3), 'red').save(out, 'PNG')
    return out.getvalue()


class Event:
    def __init__(self, uid='42', kind='private', text='', segments=()):
        self.uid, self.message_type, self.text, self.segments = uid, kind, text, segments
    def get_user_id(self): return self.uid
    def get_plaintext(self): return self.text
    def get_message(self): return self.segments


def test_private_owner_switch_queue_and_close(tmp_path):
    async def run():
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        with patch.object(plugin, 'store', store), patch.object(plugin, 'owners', return_value={'42'}), patch.object(plugin, 'fetch_media', new_callable=AsyncMock, return_value=picture()):
            owner = Event(text='开启表情包学习')
            assert await plugin.should_handle(owner)
            assert '开启' in await plugin.process_event(None, owner)
            image = Event(segments=[SimpleNamespace(type='image', data={'url':'https://gchat.qpic.cn/a'})])
            assert await plugin.should_handle(image)
            assert '1' in await plugin.process_event(None, image)
            assert store.status('42')['pending'] == 1
            assert not await plugin.should_handle(Event(uid='43', text='关闭表情包学习'))
            assert not await plugin.should_handle(Event(kind='group', text='关闭表情包学习'))
            assert '关闭' in await plugin.process_event(None, Event(text='关闭表情包学习'))
            assert not await plugin.should_handle(image)
            assert store.status('42')['pending'] == 1
    asyncio.run(run())


def test_vision_model_receives_image_and_parses_description():
    async def run():
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"category":"test","category_description":"test","description":"ok"}'))])
        with patch.object(plugin.model_dispatch, 'complete', new_callable=AsyncMock, return_value=response) as complete:
            result = await plugin.describe_image(picture(), [])
        assert result['description'] == 'ok'
        args = complete.await_args.kwargs
        assert args['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        assert args['require_vision'] is True
        assert args['extra_headers']['x-opencode-session']
    asyncio.run(run())


def test_close_during_download_keeps_already_accepted_image(tmp_path):
    async def run():
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('42', True)
        started, resume = asyncio.Event(), asyncio.Event()
        async def download(_bot, _segment):
            started.set()
            await resume.wait()
            return picture()
        with patch.object(plugin, 'store', store), patch.object(plugin, 'owners', return_value={'42'}), patch.object(plugin, 'fetch_media', side_effect=download):
            event = Event(segments=[SimpleNamespace(type='image', data={})])
            assert await plugin.should_handle(event)
            task = asyncio.create_task(plugin.process_event(None, event))
            await started.wait()
            await plugin.process_event(None, Event(text='关闭表情包学习'))
            resume.set()
            assert '已接收 1 张' in await task
            assert store.status('42')['pending'] == 1
            assert not await plugin.should_handle(event)
    asyncio.run(run())
