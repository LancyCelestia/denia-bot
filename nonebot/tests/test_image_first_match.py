r"""原始触发的端到端接线断言：图在前 + 文字在后，真的能被 MuiceBot 的 `.+` 匹配到。

    cd nonebot
    .venv\Scripts\python.exe -m pytest tests\test_image_first_match.py

这里的判定标准不是「钩子被调用」，而是**真的拿 MuiceBot 自己的 alconna 命令去解析**
`UniMessage`：2026-11-11 21:52 / 21:55 两条私聊消息就是卡在这一步，
matcher 不触发 → 没有 `收到消息文本`、没有 `[image]`、没有 ask_stream。

不连 QQ、不调模型：只构造 OneBot V11 事件对象，喂给真实的解析器。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from tests._bootstrap import ensure_nonebot
except ImportError:
    from _bootstrap import ensure_nonebot

ensure_nonebot()
import nonebot  # noqa: E402
import plugins.image_guard as guard  # noqa: E402
import image_logic as il  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    Adapter,
    Bot,
    Message as OBMessage,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender  # noqa: E402
from nonebot_plugin_alconna import UniMessage  # noqa: E402
from nonebot_plugin_alconna.uniseg.message import current_bot  # noqa: E402
from muicebot.onebot import at_event  # noqa: E402

IMG = MessageSegment.image("https://example.invalid/a.png")
TEXT = "这是啥"


def _bot() -> Bot:
    return Bot(Adapter(nonebot.get_driver()), "10000001")


def _private(segments) -> PrivateMessageEvent:
    message = OBMessage(segments)
    return PrivateMessageEvent(
        time=0,
        self_id=10000001,
        post_type="message",
        sub_type="friend",
        user_id=10000001,
        message_type="private",
        message_id=1,
        message=message,
        original_message=message.copy(),
        raw_message=str(message),
        font=0,
        sender=Sender(user_id=10000001),
        to_me=True,
    )


def _matches(event) -> bool:
    """用 MuiceBot 真正的 `at_event` 命令解析这条消息。"""
    token = current_bot.set(_bot())
    try:
        command = at_event.command()
        return bool(command.parse(UniMessage.of(event.get_message())).matched)
    finally:
        current_bot.reset(token)


def _rule():
    """补占位符的判定函数；老版本叫 `_image_only_to_me`，这样同一份用例在修复前后都能跑。"""
    return getattr(guard, "_image_needs_placeholder", None) or guard._image_only_to_me


def t_image_first_with_text_matches_after_placeholder():
    """原始触发：修复前这一条 `_matches` 一定是 False，整条消息被静默丢掉。"""
    event = _private([IMG, MessageSegment.text(TEXT)])

    assert _matches(event) is False, "前提变了：不补占位符时这条本应匹配不上"
    if asyncio.run(_rule()(event)):
        asyncio.run(guard.add_placeholder(event))
    assert _matches(event) is True, (
        "图在前 + 文字在后的消息必须能进模型；这里为 False 说明占位符没补上，"
        "消息会被 MuiceBot 的 .+ 静默丢掉（2026-11-11 21:52 / 21:55 就是这样丢的）"
    )


def t_text_first_needs_no_placeholder_and_matches():
    event = _private([MessageSegment.text("这上面讲了啥 娅娅"), IMG])

    assert asyncio.run(_rule()(event)) is False
    assert _matches(event) is True, "文字在前本来就通，不要碰它"


def t_image_only_matches_after_placeholder():
    event = _private([IMG])

    if asyncio.run(_rule()(event)):
        asyncio.run(guard.add_placeholder(event))
    assert _matches(event) is True, "纯图片消息也必须能进模型"


def t_at_prefix_still_matches():
    at = MessageSegment.at("10000001")
    event = _private([at, IMG, MessageSegment.text(TEXT)])

    if asyncio.run(_rule()(event)):
        asyncio.run(guard.add_placeholder(event))
    assert _matches(event) is True
    assert il.PLACEHOLDER in event.get_message().extract_plain_text()


def t_face_prefix_still_matches():
    """表情/抖动窗口在图片前面也会卡住 `.+`：实测 [emoji][image]这是啥 匹配失败。"""
    face = MessageSegment.face(1)
    event = _private([face, IMG, MessageSegment.text(TEXT)])

    if asyncio.run(_rule()(event)):
        asyncio.run(guard.add_placeholder(event))
    assert _matches(event) is True


def t_placeholder_is_stripped_before_model():
    """补进去的 `[图片]` 不能出现在提示词里。"""
    strip = getattr(il, "strip_placeholder", None)
    assert strip is not None, "需要有把占位符从正文剥掉的函数"
    assert strip(il.PLACEHOLDER + TEXT) == TEXT
    assert strip(TEXT) == TEXT


TESTS = [(name[2:], fn) for name, fn in sorted(globals().items()) if name.startswith("t_")]
