import asyncio
import copy
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from collections import defaultdict

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.getcwd())
import nonebot
try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelRequest
from muicebot.models import Message
from plugins import time_context
from plugins.time_context import add_time_context
from muicebot.plugin.context import set_ctx
from muicebot.plugin.hook import HookType
from muicebot.plugin.hook.manager import hook_manager


def test_history_time_and_unknown():
    original = Message(time='2026.10.28 23:30:00', userid='1', message='晚安', respond='晚安')
    unknown = Message(time='invalid', userid='1', message='早上好', respond='早')
    req = ModelRequest(prompt='早上好', system='角色', history=[original, unknown])
    asyncio.run(add_time_context(req))
    assert '北京时间' in req.system and '当前' in req.system
    assert '2026.10.28 23:30:00' in req.history[0].message
    assert '时间未知' in req.history[1].message
    assert original.message == '晚安' and unknown.message == '早上好'
    assert req.history[0].respond == '晚安'


def test_current_message_gets_correct_composite_time_fact():
    now = datetime(2026, 11, 10, 14, 10, 38, tzinfo=timezone(timedelta(hours=8)))
    answer = time_context.answer_for(Message(message='一天二十小时后是啥时候吖', userid='1'), now)
    assert '11月12日' in answer and '后天' in answer and '10:10:38' in answer


def test_quoted_old_time_does_not_become_new_time_calculation():
    req = ModelRequest(prompt='晚上给你买甜品\n被引用的消息: 明天早上十点', system='角色')
    event = SimpleNamespace(get_plaintext=lambda: '晚上给你买甜品')
    asyncio.run(add_time_context(req, event))
    assert '【本轮时间计算】' not in req.system
    assert '引用' in req.system and '当前消息' in req.system
    assert '【本轮时间约束】' in req.system and '晚上' in req.system


def test_framework_injects_current_group_event_into_time_hook():
    async def run():
        request = ModelRequest(prompt='一天二十小时后是啥时候吖', system='角色')
        event = SimpleNamespace(get_plaintext=lambda: '一天二十小时后是啥时候吖')
        set_ctx(SimpleNamespace(), event, {}, None)
        hooks = defaultdict(list, {HookType.BEFORE_MODEL_COMPLETION: [h for h in hook_manager._hooks[HookType.BEFORE_MODEL_COMPLETION] if h.function.__name__ == 'add_time_context']})
        now = datetime(2026, 11, 10, 14, 10, 38, tzinfo=timezone(timedelta(hours=8)))
        with patch.object(hook_manager, '_hooks', hooks), patch('plugins.time_context.current_time', return_value=now):
            await hook_manager.run(HookType.BEFORE_MODEL_COMPLETION, request)
        assert '2026.11.10 14:10:38' in request.system
    asyncio.run(run())


def test_merged_session_messages_use_whole_turn_not_last_event():
    async def run():
        from muicebot.utils.SessionManager import SessionManager
        from nonebot_plugin_alconna import UniMessage
        manager = SessionManager()
        await manager._put('same-session', UniMessage('一天'))
        await manager._put('same-session', UniMessage('二十小时后是啥时候吖'))
        merged = manager.merge_messages('same-session').extract_plain_text()
        assert merged == '一天二十小时后是啥时候吖'
        event = SimpleNamespace(get_plaintext=lambda: '二十小时后是啥时候吖')
        now = datetime(2026, 11, 10, 14, 10, 38, tzinfo=timezone(timedelta(hours=8)))
        answer = time_context.answer_for(Message(message=merged, userid='1'), now)
        assert '后天' in answer and '10:10:38' in answer
    asyncio.run(run())


def test_quote_with_ambiguous_relative_question_has_no_calculated_fact():
    async def run():
        merged = '那两小时后是几点？\n被引用的消息: 明天早上十点出发。'
        event = SimpleNamespace(get_plaintext=lambda: '那两小时后是几点？')
        req = ModelRequest(prompt=merged, system='角色')
        await add_time_context(req, event)
        assert '【本轮时间计算】' not in req.system
        assert time_context.answer_for(Message(message=merged, userid='1')) is None
    asyncio.run(run())


def test_quoted_time_word_can_express_a_negation():
    async def run():
        current = '晚上不能见，你什么时候方便？'
        merged = current + '\n被引用的消息: 明天早上十点见。'
        event = SimpleNamespace(get_plaintext=lambda: current)
        req = ModelRequest(prompt=merged, system='角色')
        await add_time_context(req, event)
        assert '可能是否定' in req.system
        assert '当前用户发言中的时间是' not in req.system
    asyncio.run(run())


def test_refresh_uses_original_message_time_not_refresh_time():
    message = Message(time='2026.11.10 14:10:38', message='两小时后是几点？', userid='1')
    later = datetime(2026, 11, 10, 15, 10, 38, tzinfo=timezone(timedelta(hours=8)))
    with patch('plugins.time_context.current_time', return_value=later):
        answer = time_context.answer_for(message)
    assert '16:10:38' in answer
    assert '17:10:38' not in answer


if __name__ == '__main__':
    test_history_time_and_unknown()
    print('time context test passed')
