"""QQ 机器人入口 —— SnowLuma (OneBot v11) + NoneBot2 + MuiceBot"""

import nonebot
import os
import re
import secrets
import time
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from conversation_header import configure
from private_env import get_private

# Windows 代理软件常把回环 IPv6 写进 NO_PROXY（例如 "localhost,[::1]"）。
# httpx 0.28 会把 "[::1]" 当普通域名拼成 "all://*[::1]"，解析时抛
# InvalidURL: Invalid port: ':1]'，让所有出站请求一起失败。
# 这里只删掉这两个精确条目，其余值保持原样，也不额外添加真实地址。
_LOOPBACK_NO_PROXY = frozenset({'::1', '[::1]'})


def normalize_no_proxy(value: str) -> str:
    """删掉 NO_PROXY 里的 ::1 / [::1]；没有可删条目时原样返回。"""
    if not value:
        return value
    entries = [entry for entry in re.split(r'[,\s]+', value) if entry]
    kept = [entry for entry in entries if entry not in _LOOPBACK_NO_PROXY]
    if len(kept) == len(entries):
        return value
    return ','.join(kept)


def sanitize_proxy_environment(environ=None) -> list[str]:
    """就地清理两份大小写形式的 NO_PROXY，返回实际被改动的变量名。"""
    env = os.environ if environ is None else environ
    changed = []
    for name in ('NO_PROXY', 'no_proxy'):
        if name not in env:
            continue
        current = env[name]
        cleaned = normalize_no_proxy(current)
        if cleaned != current:
            env[name] = cleaned
            changed.append(name)
    return changed


# 必须在 nonebot.init 之前：httpx 的代理表虽然创建客户端时才读取，
# 但插件导入阶段就可能立刻发起请求。
sanitize_proxy_environment()


# 启动标识：面板重启通过 QQBOT_START_TOKEN 传入新令牌，必须优先采用；
# 普通启动没有该变量，就生成一次进程级令牌，健康检查不再返回 null。
def resolve_start_token(environ=None) -> str:
    env = os.environ if environ is None else environ
    token = (env.get('QQBOT_START_TOKEN') or '').strip()
    return token or secrets.token_urlsafe(24)


START_TOKEN = resolve_start_token()
STARTED_AT = time.time()

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
        'start_token': START_TOKEN,
        'started_at': STARTED_AT,
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
