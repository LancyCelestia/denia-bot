"""Expose the current time and historical turn times to the model."""

import copy
import inspect
import re
from datetime import datetime, timedelta, timezone

from nonebot import logger
from nonebot.adapters import Event
from muicebot.llm import ModelRequest
from muicebot.models import Message as ChatMessage
from muicebot.plugin import PluginMetadata
from muicebot.plugin.context import get_bot
from muicebot.plugin.hook import HookType, hook_manager, on_before_completion
import time_calculation

__plugin_meta__ = PluginMetadata(name='time_context', description='为对话提供时间上下文', usage='自动生效')
BEIJING = timezone(timedelta(hours=8))
_TIME_WORD = re.compile(r'今晚|明晚|昨晚|今早|明早|(?:今天|明天|后天|昨天|前天)?(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|夜间)')
_SKIP_DIRECT_HOOKS = {
    ('muicebot.builtin_plugins.thought_processor', 'save_processor'),
    ('plugins.memory', 'collect'),
    ('plugins.token_usage', 'record_usage'),
}


def current_time():
    return datetime.now(BEIJING)


def answer_for(message: ChatMessage, now=None):
    """Answer only unambiguous, text-only questions about elapsed time."""
    if getattr(message, 'resources', None):
        return None
    text = getattr(message, 'message', '') or ''
    if not isinstance(text, str) or '被引用的消息:' in text or '被引用的消息：' in text:
        return None
    if now is None:
        clock = current_time()
        try:
            origin = datetime.strptime(message.time, '%Y.%m.%d %H:%M:%S').replace(tzinfo=BEIJING)
        except (AttributeError, TypeError, ValueError):
            return None
    else:
        origin = clock = now
    result = time_calculation.calculate(text, origin)
    if result is None:
        return None
    prefix = '从你提问时算起' if abs((clock - origin).total_seconds()) > 60 else '从现在算起'
    return '唔，' + prefix + '，是' + time_calculation.render(result, clock) + '。'


@on_before_completion(priority=8)
async def add_time_context(request: ModelRequest, event: Event = None) -> None:
    now = current_time()
    request.system = (request.system or '') + (
        f'\n当前北京时间：{now:%Y.%m.%d %H:%M:%S}。历史对话的时间标注适用于该轮用户发言及回复；'
        '结合当前时间判断先后和间隔，时间未知时不要猜测，避免把久前的对话说成刚刚发生。'
        '当前消息为新事件明确指定了时间时，应以当前消息为准；引用和历史只是上下文，'
        '不要把其中的具体时间移给新事件，也不要在未说明时擅自补充具体时刻。'
    )
    try:
        text = request.prompt or ''
        has_quote = '被引用的消息:' in text or '被引用的消息：' in text
        if has_quote:
            current = event.get_plaintext() if event is not None else ''
            words = list(dict.fromkeys(_TIME_WORD.findall(current)))
            if len(words) == 1:
                request.system += (f'\n【本轮时间约束】当前用户发言包含“{words[0]}”，'
                                   '它可能是否定或条件，必须结合完整句意，不要默认是确定安排。'
                                   '不要把引用里的其他时间套到当前新安排；除非用户直接询问旧安排，'
                                   '不要主动重复或追问引用中的时间。')
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    history = []
    for item in request.history or []:
        message = copy.copy(item)
        try:
            stamp = datetime.strptime(item.time or '', '%Y.%m.%d %H:%M:%S').strftime('%Y.%m.%d %H:%M:%S')
            label = f'北京时间 {stamp}'
        except (TypeError, ValueError):
            label = '时间未知'
        message.message = f'〔对话时间：{label}〕\n{item.message or ""}'
        history.append(message)
    request.history = history


async def record_time_answer(muice, session, message, event, answer):
    """Preserve applicable post-chat hooks without consuming model-only state."""
    message.respond = answer
    hooks = list(hook_manager._hooks.get(HookType.ON_FINISHING_CHAT, ()))
    for hook in sorted(hooks, key=lambda item: item.priority):
        function = hook.function
        if (function.__module__, function.__name__) in _SKIP_DIRECT_HOOKS:
            continue
        try:
            if hook.stream is False:
                continue
            if hook.rule and not await hook.rule(get_bot(), event, {}):
                continue
            args = await hook_manager._inject_dependencies(function, message)
            result = function(**args)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.warning(f'[time] 完成钩子 {function.__name__} 失败：{type(e).__name__}')
        finally:
            message.respond = answer
    try:
        await muice.database.add_item(session, message)
        # MuiceBot's refresh handler commits before consuming a streaming reply.
        if session is not None and event.get_plaintext().strip() in ('.refresh', '/refresh'):
            await session.commit()
    except Exception as e:
        logger.warning(f'[time] 保存对话失败：{type(e).__name__}')
