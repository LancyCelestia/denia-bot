"""回复守卫在真实 `Muice.ask` / `ask_stream` 收尾路径上的接线断言。

关键点不是「守卫函数返回了什么」，而是 MuiceBot **有没有落库/跑 on_finish_chat**：
纯计划首轮必须在 `total_reply` 为空的分支返回，不能留下一条空 assistant 历史。
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._bootstrap import ensure_nonebot

ensure_nonebot()
from muicebot.llm import ModelCompletions, ModelStreamCompletions  # noqa: E402
from muicebot.models import Message  # noqa: E402
from muicebot.muice import Muice  # noqa: E402
from muicebot.plugin.context import set_ctx  # noqa: E402
from muicebot.plugin.hook import HookType  # noqa: E402
from muicebot.plugin.hook.manager import hook_manager  # noqa: E402
import plugins.model_pref as pref  # noqa: E402
import plugins.reply_guard  # noqa: E402,F401

#: 其他 wiring 用例会把 `pref.ORIGINAL_*` 换成替身；这里在收集阶段先扣住原始实现，
#: 避免整份 pytest 同进程跑时受用例顺序影响。
ORIGINAL_STREAM = pref.ORIGINAL_STREAM
ORIGINAL_ASK = pref.ORIGINAL_ASK

PLANNING = "Initial response: I should keep the character tone."


class Event:
    message_type = "private"
    self_id = "test"

    def get_user_id(self):
        return "1"

    def get_session_id(self):
        return "private_1"


def _fake_muice(model):
    return SimpleNamespace(
        model=model,
        model_config=SimpleNamespace(function_call=False, multimodal=False),
        system_prompt="角色",
        _prepare_prompt=AsyncMock(return_value="打亖你😈"),
        _prepare_history=AsyncMock(return_value=[]),
        database=SimpleNamespace(add_item=AsyncMock()),
    )


def _hooks(*, thought=True):
    wanted = {"plugins.reply_guard"}
    if thought:
        wanted.add("muicebot.builtin_plugins.thought_processor")
    return defaultdict(
        list,
        {
            kind: [h for h in registered if h.function.__module__ in wanted]
            for kind, registered in hook_manager._hooks.items()
        },
    )


def _run(coro):
    return asyncio.run(coro)


def test_streaming_pure_planning_is_not_persisted():
    async def run():
        set_ctx(SimpleNamespace(send=AsyncMock()), Event(), {}, None)

        async def planning_stream():
            yield ModelStreamCompletions(PLANNING)

        model = SimpleNamespace(is_running=True, ask=AsyncMock(return_value=planning_stream()))
        pref._ensure_answer_guard(model)
        fake = _fake_muice(model)
        message = Message(time="2026.11.23 02:03:24", message="打亖你😈", userid="1", groupid="-1")

        with patch.object(hook_manager, "_hooks", _hooks()), \
             patch.object(Muice, "ask_stream", ORIGINAL_STREAM):
            chunks = [chunk.chunk async for chunk in Muice.ask_stream(fake, None, message)]

        assert "".join(chunks).startswith("模型回复为空"), chunks
        assert message.respond == ""
        fake.database.add_item.assert_not_awaited()

    _run(run())


def test_streaming_normal_answer_still_persists():
    async def run():
        set_ctx(SimpleNamespace(send=AsyncMock()), Event(), {}, None)

        async def normal_stream():
            yield ModelStreamCompletions("……嗯")
            yield ModelStreamCompletions("，我在的。")

        model = SimpleNamespace(is_running=True, ask=AsyncMock(return_value=normal_stream()))
        pref._ensure_answer_guard(model)
        fake = _fake_muice(model)
        message = Message(time="2026.11.23 02:03:24", message="打亖你😈", userid="1", groupid="-1")

        with patch.object(hook_manager, "_hooks", _hooks()), \
             patch.object(Muice, "ask_stream", ORIGINAL_STREAM):
            chunks = [chunk.chunk async for chunk in Muice.ask_stream(fake, None, message)]

        text = "".join(chunks).strip()
        assert text == "……嗯，我在的。", text
        assert message.respond.strip() == text
        fake.database.add_item.assert_awaited_once()

    _run(run())


def test_non_streaming_normal_answer_still_persists():
    async def run():
        set_ctx(SimpleNamespace(send=AsyncMock()), Event(), {}, None)
        model = SimpleNamespace(
            is_running=True,
            ask=AsyncMock(return_value=ModelCompletions(text="……嗯，我在的。")),
        )
        pref._ensure_answer_guard(model)
        fake = _fake_muice(model)
        message = Message(time="2026.11.23 02:03:24", message="打亖你😈", userid="1", groupid="-1")

        with patch.object(hook_manager, "_hooks", _hooks(thought=False)), \
             patch.object(Muice, "ask", ORIGINAL_ASK):
            result = await Muice.ask(fake, None, message)

        assert result.text == "……嗯，我在的。", result.text
        assert message.respond == result.text
        fake.database.add_item.assert_awaited_once()

    _run(run())


def test_non_streaming_pure_planning_is_not_persisted():
    async def run():
        set_ctx(SimpleNamespace(send=AsyncMock()), Event(), {}, None)
        model = SimpleNamespace(
            is_running=True,
            ask=AsyncMock(return_value=ModelCompletions(text=PLANNING)),
        )
        pref._ensure_answer_guard(model)
        fake = _fake_muice(model)
        message = Message(time="2026.11.23 02:03:24", message="打亖你😈", userid="1", groupid="-1")

        with patch.object(hook_manager, "_hooks", _hooks(thought=False)), \
             patch.object(Muice, "ask", ORIGINAL_ASK):
            result = await Muice.ask(fake, None, message)

        assert result.text.startswith("模型回复为空"), result.text
        assert message.respond == ""
        fake.database.add_item.assert_not_awaited()

    _run(run())
