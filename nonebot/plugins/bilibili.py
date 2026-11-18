"""Owner-managed Bilibili dynamic subscriptions; defaults to disabled."""
from nonebot import get_bots, get_driver, get_plugin_config, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from muicebot.plugin import PluginMetadata

from bilibili.assembly import event_command
from bilibili.config import Config
from bilibili.model import HELP
from bilibili.runtime import Runtime
import gate_logic as gl

__plugin_meta__=PluginMetadata(name='bilibili',description='B站动态订阅卡片与视频搜索',
    usage='.b站订阅 <UID> / .b站退订 <UID> / .b站订阅列表 / .b站搜索 <关键词> / .b站选 <序号>')
gl.register_help_rows(HELP)
runtime=Runtime(get_plugin_config(Config),get_bots)


@get_driver().on_startup
async def start_plugin():
    await runtime.start()


@get_driver().on_shutdown
async def stop_plugin():
    await runtime.close()


async def rule(bot: Bot,event: Event):
    return event_command(bot,event) is not None


matcher=on_message(rule=rule,priority=1,block=True)


@matcher.handle()
async def handle(bot: Bot,event: Event):
    parsed=event_command(bot,event)
    if parsed is None:return
    scope,user,text=parsed
    owners={str(value) for value in (get_driver().config.superusers or ())}
    response=await runtime.dispatch(scope,user,text,is_owner=user in owners)
    # Treat every remote/user string as literal text, never CQ source.
    await matcher.finish(MessageSegment.text(response))
