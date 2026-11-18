import asyncio
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from collections import defaultdict

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelRequest, ModelCompletions, ModelStreamCompletions
from muicebot.models import Message
from plugins import meme
import meme_logic
import meme_learning
import model_pref_logic
from muicebot.muice import Muice
from muicebot.plugin.context import set_ctx
from muicebot.plugin.hook.manager import hook_manager


class Event:
    message_type = 'group'
    group_id = 123
    def get_user_id(self):
        return '42'
    def get_session_id(self):
        return 'group_123_42'


def library(tmp_path):
    lib = meme_logic.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    data = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(data, format='PNG')
    lib.add_image(category, data.getvalue())
    return lib, category, data.getvalue()


def test_stream_and_nonstream_send_selected_image(tmp_path):
    async def run():
        lib, category, data = library(tmp_path)
        event, bot = Event(), SimpleNamespace(send=AsyncMock())
        with patch.object(meme, 'library', lib):
            req = ModelRequest(prompt='好消息', system='人格')
            await meme.inject(req, event)
            assert category in req.system and '庆祝' in req.system
            chunks = [ModelStreamCompletions('太好'), ModelStreamCompletions('了！[[me'), ModelStreamCompletions('me:' + category + ']]')]
            for chunk in chunks:
                meme.filter_chunk(chunk, meme._states[id(event)])
            assert ''.join(c.chunk for c in chunks) == '太好了！'
            result = ModelCompletions(text='太好了！')
            msg = Message(message='好消息', userid='42', groupid='123', respond='太好了！')
            await meme.finish(result, msg, event, bot)
            assert result.resources[0].raw == data
            req = ModelRequest(prompt='好消息')
            await meme.inject(req, event)
            result = ModelCompletions(text='开心！[[meme:' + category + ']]')
            msg.respond = result.text
            await meme.finish(result, msg, event, bot)
            assert result.text == msg.respond == '开心！'
            assert result.resources[0].raw == data
    asyncio.run(run())


def test_scope_and_concurrent_events_are_isolated(tmp_path):
    async def run():
        lib, category, _ = library(tmp_path)
        with patch.object(meme, 'library', lib):
            first, second = Event(), Event()
            await meme.inject(ModelRequest(prompt='a'), first)
            await meme.inject(ModelRequest(prompt='b'), second)
            meme.filter_chunk(ModelStreamCompletions('a[[meme:' + category + ']]'), meme._states[id(first)])
            b = ModelStreamCompletions('b')
            meme.filter_chunk(b, meme._states[id(second)])
            result = ModelCompletions(text='b')
            await meme.finish(result, Message(message='b', userid='42', groupid='123'), second, SimpleNamespace(send=AsyncMock()))
            assert not result.resources
            result = ModelCompletions(text='a')
            await meme.finish(result, Message(message='a', userid='42', groupid='123'), first, SimpleNamespace(send=AsyncMock()))
            assert len(result.resources) == 1
            lib.set_rules(True, ['group:123'])
            request = ModelRequest(prompt='a', system='人格')
            await meme.inject(request, Event())
            assert request.system == '人格'
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['image', 'only_image', 'prefix', 'trailing'])
def test_real_muice_stream_through_model_timeout_wrapper(tmp_path, kind):
    async def run():
        lib, category, data = library(tmp_path)
        event, bot = Event(), SimpleNamespace(send=AsyncMock())
        set_ctx(bot, event, {}, None)
        async def response():
            texts = {'image': ['太好了', '[[me', 'me:' + category + ']]'],
                     'only_image': ['[[meme:' + category + ']]'],
                     'prefix': ['['], 'trailing': ['正常回复[']}[kind]
            for text in texts:
                yield ModelStreamCompletions(text)
        model = SimpleNamespace(is_running=True, ask=AsyncMock(return_value=response()))
        fake = SimpleNamespace(model=model, model_config=SimpleNamespace(function_call=False, multimodal=False), system_prompt='人格',
                               _prepare_prompt=AsyncMock(return_value='好消息'), _prepare_history=AsyncMock(return_value=[]),
                               database=SimpleNamespace(add_item=AsyncMock()))
        msg = Message(message='好消息', userid='42', groupid='123')
        hooks = defaultdict(list, {kind: [h for h in registered if h.function.__module__ in (meme.__name__, 'muicebot.builtin_plugins.thought_processor')]
                                  for kind, registered in hook_manager._hooks.items()})
        with patch.object(meme, 'library', lib), patch.object(hook_manager, '_hooks', hooks):
            output = []
            async for chunk in model_pref_logic.with_rescue({}, lambda: meme.filter_stream(Muice.ask_stream(fake, None, msg), event), enabled=False, timeout=2):
                output.append(chunk)
        expected = {'image': '太好了', 'only_image': '（表情）', 'prefix': '[', 'trailing': '正常回复['}[kind]
        assert ''.join(c.chunk for c in output).strip() == expected
        assert [r.raw for c in output for r in c.resources] == ([data] if kind in ('image', 'only_image') else [])
        assert msg.respond == expected
    asyncio.run(run())


def test_library_read_does_not_block_other_chat_tasks(tmp_path):
    async def run():
        lib, _, _ = library(tmp_path)
        original = lib.enabled
        def slow(scope):
            time.sleep(.25)
            return original(scope)
        lib.enabled = slow
        with patch.object(meme, 'library', lib):
            pending = asyncio.create_task(meme.inject(ModelRequest(prompt='hello'), Event()))
            started = time.monotonic()
            await asyncio.sleep(.02)
            assert time.monotonic() - started < .18
            await pending
    asyncio.run(run())


def test_interrupted_stream_releases_event_state(tmp_path):
    async def run():
        lib, _, _ = library(tmp_path)
        event = Event()
        with patch.object(meme, 'library', lib):
            await meme.inject(ModelRequest(prompt='hello'), event)
            async def source():
                yield ModelStreamCompletions('hello')
                await asyncio.sleep(10)
            stream = meme.filter_stream(source(), event)
            await stream.__anext__()
            await stream.aclose()
            assert id(event) not in meme._states
    asyncio.run(run())


def test_reply_uses_semantic_match_within_model_selected_category(tmp_path):
    async def run():
        lib = meme_logic.Library(tmp_path)
        learned = meme_learning.Store(lib)
        learned.set_enabled('42', True)
        red = io.BytesIO(); Image.new('RGB', (2, 2), 'red').save(red, 'PNG')
        blue = io.BytesIO(); Image.new('RGB', (2, 2), 'blue').save(blue, 'PNG')
        for data, desc, vector in ((red.getvalue(), '庆祝', [1, 0]), (blue.getvalue(), '问候', [0, 1])):
            learned.enqueue('42', data)
            job = learned.next_job()
            learned.complete(job['id'], '开心', '高兴', desc, vector)
        category = lib.categories()[0]['id']
        event = Event()
        with patch.object(meme, 'library', lib), patch.object(meme.meme_learning, 'embed', new_callable=AsyncMock, return_value=[0, 1]):
            await meme.inject(ModelRequest(prompt='打个招呼'), event)
            result = ModelCompletions(text='你好！[[meme:' + category + ']]')
            msg = Message(message='打个招呼', userid='42', groupid='123', respond=result.text)
            await meme.finish(result, msg, event, SimpleNamespace(send=AsyncMock()))
            assert result.resources[0].raw == blue.getvalue()
    asyncio.run(run())


def test_semantic_service_failure_keeps_image_reply(tmp_path):
    async def run():
        lib = meme_logic.Library(tmp_path)
        learned = meme_learning.Store(lib)
        learned.set_enabled('42', True)
        data = io.BytesIO(); Image.new('RGB', (2, 2), 'red').save(data, 'PNG')
        learned.enqueue('42', data.getvalue())
        learned.complete(learned.next_job()['id'], '开心', '快乐', '庆祝', [1, 0])
        category = lib.categories()[0]['id']
        event = Event()
        with patch.object(meme, 'library', lib), patch.object(meme.meme_learning, 'embed', new_callable=AsyncMock, side_effect=TimeoutError):
            await meme.inject(ModelRequest(prompt='庆祝'), event)
            result = ModelCompletions(text='好耶[[meme:' + category + ']]')
            await meme.finish(result, Message(message='庆祝', userid='42', groupid='123'), event, SimpleNamespace(send=AsyncMock()))
            assert result.text == '好耶'
            assert result.resources[0].raw == data.getvalue()
    asyncio.run(run())
