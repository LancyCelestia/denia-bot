"""QQ 机器人入口 —— SnowLuma (OneBot v11) + NoneBot2 + MuiceBot"""

import nonebot
import os
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from conversation_header import configure
from private_env import get_private

# SDK clients created during this process share the ID; each restart gets a new one.
os.environ['OPENAI_CUSTOM_HEADERS'] = configure()

# 初始化 NoneBot（读取 .env）
nonebot.init(command_start={'.'})

driver = nonebot.get_driver()

# 注册 OneBot v11 适配器（SnowLuma 走这个协议）
driver.register_adapter(OneBotV11Adapter)

@nonebot.get_app().get('/dashboard-health', include_in_schema=False)
async def dashboard_health():
    target_id = get_private('QQBOT_SELF_ID')
    bots = nonebot.get_bots()
    return {
        'service': 'qqbot-nonebot',
        'ready': True,
        'connected': target_id in bots if target_id else bool(bots),
        'pid': os.getpid(),
        'start_token': os.environ.get('QQBOT_START_TOKEN'),
    }

# MuiceBot —— LLM 聊天 + 人格模板
nonebot.load_plugin("muicebot")

# gsuid_core 连接器（NoneBot2 侧）：core 是独立后端，默认启用；
# 未部署 core 时设 GSUID_CORE_ENABLE=0 即可完全不加载。
if os.getenv("GSUID_CORE_ENABLE", "1").strip().lower() not in {"0", "false", "no"}:
    nonebot.load_plugin("GenshinUID")
    # 适配器默认按 NoneBot 的 command_start 剥掉 "." 再转发；core 现在要求
    # 严格的 "." 前缀，所以这里保留点号交给 core 统一剥离。
    from GenshinUID import receive as _gs_receive
    _gs_receive.command_starts = lambda: set()

from command_policy import install_command_rules
install_command_rules()

# 本地插件目录由 MuiceBot 在加载时统一处理（它需要 __plugin_meta__）。
# 这里不能再提前 load_plugins，否则插件 import muicebot 时它尚未注册。


if __name__ == "__main__":
    nonebot.run()
