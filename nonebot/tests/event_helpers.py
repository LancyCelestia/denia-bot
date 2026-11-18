"""OneBot V11 群/私聊事件构造器（不连 QQ/不发网络）。"""

import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._bootstrap import ensure_nonebot  # noqa: E402

ensure_nonebot()

from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

SELF = "10000001"
GROUP = 123456789


def _msg(*segments) -> Message:
    message = Message()
    for segment in segments:
        message += segment
    return message


def group_event(
    text: str, *, at_self: bool = False, at_other: bool = False, user: str = "123456"
) -> GroupMessageEvent:
    message = _msg(
        *([MessageSegment.at(SELF)] if at_self else []),
        *([MessageSegment.at("999999")] if at_other else []),
        *([MessageSegment.text(text)] if text else []),
    )
    return GroupMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="normal",
        user_id=int(user),
        group_id=GROUP,
        message_type="group",
        message_id=1,
        message=message,
        raw_message=str(message),
        font=0,
        sender={"card": "", "nickname": "t", "role": "member", "title": ""},
        to_me=at_self,
    )


def private_event(text: str, user: str = "123456") -> PrivateMessageEvent:
    message = _msg(MessageSegment.text(text)) if text else Message()
    return PrivateMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="friend",
        user_id=int(user),
        message_type="private",
        message_id=2,
        message=message,
        raw_message=str(message),
        font=0,
        sender={"card": "", "nickname": "t", "role": "friend", "title": ""},
        to_me=True,
    )
