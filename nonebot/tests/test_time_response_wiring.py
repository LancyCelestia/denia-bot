import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelCompletions, ModelRequest, ModelStreamCompletions
from muicebot.models import Message
from muicebot.muice import Muice
from muicebot.plugin.context import set_ctx
from muicebot.plugin.hook.manager import hook_manager
from muicebot.plugin.hook import HookType
from plugins import meme, model_pref, time_context
import meme_logic

NOW = datetime(2026, 11, 10, 14, 10, 38, tzinfo=timezone(timedelta(hours=8)))


class Event:
    message_type = 'private'
    self_id = 'test'
    def get_user_id(self): return '1'
    def get_session_id(self): return 'private_1'
    def get_plaintext(self): return '一天二十小时后是啥时候吖'


def test_streaming_wrong_model_arithmetic_is_replaced_and_history_is_correct(tmp_path):
    async def run():
        event = Event()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        async def response():
            yield ModelStreamCompletions('明天早上')
            yield ModelStreamCompletions('六点。')
        model = SimpleNamespace(is_running=True, ask=AsyncMock(return_value=response()))
        fake = SimpleNamespace(model=model, model_config=SimpleNamespace(function_call=False, multimodal=False),
            system_prompt='角色', _prepare_prompt=AsyncMock(return_value=event.get_plaintext()),
            _prepare_history=AsyncMock(return_value=[]), database=SimpleNamespace(add_item=AsyncMock()))
        message = Message(time='2026.11.10 14:10:38', message=event.get_plaintext(), userid='1', groupid='-1')
        wanted = {meme.__name__, time_context.__name__, 'muicebot.builtin_plugins.thought_processor'}
        hooks = defaultdict(list, {kind:[h for h in registered if h.function.__module__ in wanted]
                                  for kind, registered in hook_manager._hooks.items()})
        observed = []
        async def observer(message: Message):
            observed.append(message.respond)
        hooks[HookType.ON_FINISHING_CHAT].append(SimpleNamespace(function=observer, priority=20, stream=None, rule=None))
        with patch.object(hook_manager, '_hooks', hooks), patch.object(meme, 'library', meme_logic.Library(tmp_path)), \
             patch.object(Muice, 'ask_stream', Muice.ask_stream), patch.object(Muice, 'ask', Muice.ask), \
             patch.object(time_context, 'current_time', return_value=NOW), patch.object(model_pref, 'ensure_primary'), \
             patch.object(model_pref, 'state', return_value={}), patch.object(model_pref, '_flag', return_value=False):
            meme.install_stream_filter()
            chunks = [chunk.chunk async for chunk in Muice.ask_stream(fake, None, message)]
        text = ''.join(chunks).strip()
        assert '11月12日' in text and '后天' in text and '10:10:38' in text
        assert '明天早上六点' not in text
        assert message.respond == text
        assert observed == [text]
        fake.database.add_item.assert_awaited_once()
        model.ask.assert_not_awaited()
    asyncio.run(run())


def test_non_streaming_wrong_result_is_replaced():
    async def run():
        event = Event()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        message = Message(time='2026.11.10 14:10:38', message=event.get_plaintext(), userid='1', groupid='-1')
        fake = SimpleNamespace(model=None, database=SimpleNamespace(add_item=AsyncMock()))
        wanted = {time_context.__name__, 'muicebot.builtin_plugins.thought_processor'}
        hooks = defaultdict(list, {kind:[h for h in registered if h.function.__module__ in wanted]
                                  for kind, registered in hook_manager._hooks.items()})
        with patch.object(hook_manager, '_hooks', hooks), patch.object(time_context, 'current_time', return_value=NOW), \
             patch.object(Muice, 'ask_stream', Muice.ask_stream), patch.object(Muice, 'ask', Muice.ask):
            meme.install_stream_filter()
            result = await Muice.ask(fake, None, message)
        assert '11月12日' in result.text and '10:10:38' in result.text
        assert message.respond == result.text
        assert result.succeed
        fake.database.add_item.assert_awaited_once()
    asyncio.run(run())


def test_stream_records_only_after_the_answer_was_consumed():
    async def run():
        event = Event()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        fake = SimpleNamespace(database=SimpleNamespace(add_item=AsyncMock()))
        message = Message(time='2026.11.10 14:10:38', message=event.get_plaintext(), userid='1', groupid='-1')
        with patch.object(time_context, 'current_time', return_value=NOW), \
             patch.object(Muice, 'ask_stream', Muice.ask_stream), patch.object(Muice, 'ask', Muice.ask):
            meme.install_stream_filter()
            stream = Muice.ask_stream(fake, None, message)
            chunk = await stream.__anext__()
            assert '后天' in chunk.chunk
            await stream.aclose()
        fake.database.add_item.assert_not_awaited()
    asyncio.run(run())


def test_direct_answer_does_not_consume_other_turn_thought_cache():
    async def run():
        from muicebot.builtin_plugins import thought_processor
        event = Event()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        fake = SimpleNamespace(database=SimpleNamespace(add_item=AsyncMock()))
        message = Message(message=event.get_plaintext(), userid='1', groupid='-1')
        cache = thought_processor.ProcessCache(result='另一条消息尚在生成')
        thought_processor._PROCESSCACHES[event.get_session_id()] = cache
        try:
            await time_context.record_time_answer(fake, None, message, event, '正确时间')
            assert thought_processor._PROCESSCACHES[event.get_session_id()] is cache
            assert cache.result == '另一条消息尚在生成'
        finally:
            thought_processor._PROCESSCACHES.pop(event.get_session_id(), None)
    asyncio.run(run())


def test_direct_answer_still_runs_later_hooks_if_one_fails():
    async def run():
        event = Event()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        fake = SimpleNamespace(database=SimpleNamespace(add_item=AsyncMock()))
        message = Message(message=event.get_plaintext(), userid='1', groupid='-1')
        observed = []
        async def fail(message: Message): raise ValueError('test')
        async def count(message: Message): observed.append(message.respond)
        hooks = defaultdict(list, {HookType.ON_FINISHING_CHAT: [
            SimpleNamespace(function=fail, priority=5, stream=None, rule=None),
            SimpleNamespace(function=count, priority=50, stream=None, rule=None),
        ]})
        with patch.object(hook_manager, '_hooks', hooks):
            await time_context.record_time_answer(fake, None, message, event, '正确时间')
        assert observed == ['正确时间']
        fake.database.add_item.assert_awaited_once()
    asyncio.run(run())


def test_streaming_refresh_commits_new_answer_after_sending():
    async def run():
        class RefreshEvent(Event):
            def get_plaintext(self): return '.refresh'
        event = RefreshEvent()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        fake = SimpleNamespace(model=None, database=SimpleNamespace(add_item=AsyncMock()))
        session = SimpleNamespace(commit=AsyncMock())
        message = Message(time='2026.11.10 14:10:38', message='两小时后是几点？', userid='1', groupid='-1')
        later = NOW + timedelta(hours=1)
        hooks = defaultdict(list)
        with patch.object(hook_manager, '_hooks', hooks), patch.object(time_context, 'current_time', return_value=later), \
             patch.object(Muice, 'ask_stream', Muice.ask_stream), patch.object(Muice, 'ask', Muice.ask):
            meme.install_stream_filter()
            output = ''.join([part.chunk async for part in Muice.ask_stream(fake, session, message)])
        assert '16:10:38' in output and '17:10:38' not in output
        fake.database.add_item.assert_awaited_once_with(session, message)
        session.commit.assert_awaited_once()
    asyncio.run(run())


def test_refresh_persists_replacement_in_real_sqlite(tmp_path):
    async def run():
        from sqlalchemy.engine import URL
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from muicebot.database import MessageORM, Msg, User
        class RefreshEvent(Event):
            def get_plaintext(self): return '.refresh'
        event = RefreshEvent()
        set_ctx(SimpleNamespace(send=AsyncMock()), event, {}, None)
        engine = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'history.sqlite3')))
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Msg.__table__.create)
                await connection.run_sync(User.__table__.create)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                original = Message(time='2026.11.10 14:10:38', message='两小时后是几点？', respond='旧回答', userid='1', groupid='-1')
                await MessageORM.add_item(session, original)
                await session.commit()
                await MessageORM.mark_history_as_unavailable(session, '1', 1)
                await session.commit()  # MuiceBot's refresh handler commits before consuming the stream.
                fake = SimpleNamespace(model=None, database=MessageORM())
                hooks = defaultdict(list)
                with patch.object(hook_manager, '_hooks', hooks), patch.object(time_context, 'current_time', return_value=NOW + timedelta(hours=1)), \
                     patch.object(Muice, 'ask_stream', Muice.ask_stream), patch.object(Muice, 'ask', Muice.ask):
                    meme.install_stream_filter()
                    output = ''.join([part.chunk async for part in Muice.ask_stream(fake, session, original)])
                assert '16:10:38' in output
            async with factory() as verify:
                history = await MessageORM.get_user_history(verify, '1')
                assert len(history) == 1 and '16:10:38' in history[0].respond
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_stream_continues_if_time_plugin_is_not_available():
    async def run():
        event = object()
        async def source(_self, *_args, **_kwargs):
            yield ModelStreamCompletions('正常回复')
        with patch.dict(sys.modules, {'plugins.time_context': SimpleNamespace()}), \
             patch.object(Muice, 'ask_stream', source), patch.object(Muice, 'ask', Muice.ask), \
             patch.object(meme, 'get_event', return_value=event):
            meme.install_stream_filter()
            chunks = [chunk.chunk async for chunk in Muice.ask_stream(object(), None, None)]
        assert ''.join(chunks) == '正常回复'
    asyncio.run(run())


def test_stream_continues_if_time_plugin_fails():
    async def run():
        def fail(_message): raise RuntimeError('temporary failure')
        async def source(_self, *_args, **_kwargs):
            yield ModelStreamCompletions('正常回复')
        with patch.dict(sys.modules, {'plugins.time_context': SimpleNamespace(answer_for=fail, record_time_answer=AsyncMock())}), \
             patch.object(Muice, 'ask_stream', source), patch.object(Muice, 'ask', Muice.ask), \
             patch.object(meme, 'get_event', return_value=object()):
            meme.install_stream_filter()
            chunks = [chunk.chunk async for chunk in Muice.ask_stream(object(), None, SimpleNamespace(message='test'))]
        assert ''.join(chunks) == '正常回复'
    asyncio.run(run())
