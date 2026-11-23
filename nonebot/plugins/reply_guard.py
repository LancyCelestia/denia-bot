"""把模型未标记的自述计划从历史里去掉。

发送面由 ``model_pref`` 的流式守卫负责；这里补历史面：``on_finish_chat`` 在 MuiceBot
落库前跑，优先级排在 meme 的 ``clean_history``（2）之后，避免和表情标记的清理互相覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

from nonebot import logger
from muicebot.models import Message
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_finish_chat

try:
    import reply_guard_logic as rgl
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import reply_guard_logic as rgl

__plugin_meta__ = PluginMetadata(
    name="reply_guard",
    description="去掉模型未加标签的计划文本，避免思考链进入聊天与历史",
    usage="自动生效",
)


@on_finish_chat(priority=3)
def clean_history(message: Message) -> None:
    clean, dropped = rgl.sanitize(message.respond or "")
    if dropped:
        logger.warning(f"[reply-guard] 历史里去掉一段计划文本，剩余 {len(clean)} 字")
        message.respond = clean
