# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""预警身份与统一口吻。

V1 规则（`docs/research/disaster-v1-ux-spec.md`）：
- 同一预警只播报一次，统一使用达妮娅口吻；
- 去重按事件身份，不按报次或内容哈希：
  - 地震：`source + event_id`（同一地震的多报只发首条）
  - 气象/海啸：`alert_id`
  - 台风：`typhoon_id`；停编通知单独去重
"""
from __future__ import annotations

from typing import Any

NEUTRAL = 'neutral'
CHARACTER = 'character'


def alert_identity(kind: str, item: Any) -> str:
    """同一预警的稳定标识，用于永久去重（不含报次/内容哈希）。"""
    if kind == 'earthquake':
        return f'alert:earthquake:{getattr(item, "source", "")}:{getattr(item, "event_id", "")}'
    if kind == 'weather':
        return f'alert:weather:{getattr(item, "alert_id", "")}'
    if kind == 'tsunami':
        return f'alert:tsunami:{getattr(item, "alert_id", "")}'
    if kind == 'typhoon':
        return f'alert:typhoon:{getattr(item, "typhoon_id", "")}'
    if kind == 'typhoon_stop':
        return f'alert:typhoon-stop:{getattr(item, "typhoon_id", "")}'
    return (
        f'alert:{kind}:'
        f'{getattr(item, "alert_id", "") or getattr(item, "typhoon_id", "") or getattr(item, "event_id", "") or id(item)}'
    )
