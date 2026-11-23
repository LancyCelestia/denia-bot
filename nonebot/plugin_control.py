"""Persistent plugin switches and the startup load filter.

The settings live under ``nonebot/data`` so a normal ``git pull`` never
overwrites them.  ``bot.py`` installs the filter before MuiceBot scans the
local ``plugins`` directory; the dashboard only edits the same file.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / 'data' / 'plugins.json'
DEFAULT = {'onboarded': False, 'disabled': []}

# A small catalog is intentional: the dashboard must not expose arbitrary
# module names, and core wiring must stay loadable even if a file is edited by hand.
PLUGINS = (
    {'id': 'gate', 'module': 'plugins.gate', 'label': '消息入口与触发规则',
     'description': '群聊 @、回复和指令路由；关闭后聊天入口不完整。', 'core': True},
    {'id': 'model_pref', 'module': 'plugins.model_pref', 'label': '模型首选与故障切换',
     'description': '首选模型和模型降级；关闭后不加载模型选择外壳。', 'core': True},
    {'id': 'reply_guard', 'module': 'plugins.reply_guard', 'label': '计划文本守卫',
     'description': '识别并去掉未加标签的自述计划，避免思考链进入聊天与历史。', 'core': True},
    {'id': 'name_guard', 'module': 'plugins.name_guard', 'label': '昵称与称呼保护',
     'description': 'MuiceBot 的昵称读取超时与称呼兼容补丁。', 'core': True},
    {'id': 'prompt_guard', 'module': 'plugins.prompt_guard', 'label': '提示词场景隔离',
     'description': '跨群/私聊历史隔离和发言人署名整理。', 'core': True},
    {'id': 'quote_fix', 'module': 'plugins.quote_fix', 'label': '引用回复修复',
     'description': '把 QQ 引用消息转换成 MuiceBot 可读的文本。', 'core': True},
    {'id': 'image_guard', 'module': 'plugins.image_guard', 'label': '图片理解',
     'description': '压缩并提交本轮图片；关闭后图片不进入模型。', 'core': True},
    {'id': 'time_context', 'module': 'plugins.time_context', 'label': '时间问答',
     'description': '处理与当前时间有关的简单问题。', 'core': True},
    {'id': 'gateway_session', 'module': 'plugins.gateway_session', 'label': '会话上下文',
     'description': '模型调用所需的会话/网关接线。', 'core': True},
    {'id': 'superusers', 'module': 'plugins.superusers', 'label': 'QQ 超级管理员',
     'description': '从面板维护 QQ 端管理指令的白名单。', 'core': True, 'guide': True},
    {'id': 'master_aware', 'module': 'plugins.master_aware', 'label': '主人识别',
     'description': '根据配置识别主人和称呼。', 'core': True, 'guide': True},
    {'id': 'statuscard', 'module': 'plugins.statuscard', 'label': '服务器状态卡',
     'description': '资源采样、状态卡、告警、日报和周报。', 'guide': True},
    {'id': 'bilibili', 'module': 'plugins.bilibili', 'label': 'B站动态订阅',
     'description': '按会话订阅 UP 主动态并推送卡片。', 'guide': True},
    {'id': 'disaster_warning', 'module': 'plugins.disaster_warning', 'label': '灾害预警',
     'description': '气象、海啸和地震监测与推送。', 'guide': True},
    {'id': 'epic_free', 'module': 'plugins.epic_free', 'label': 'Epic 喜加一',
     'description': '免费游戏查询和换期推送。', 'guide': True},
    {'id': 'game_events', 'module': 'plugins.game_events', 'label': '游戏活动',
     'description': '游戏活动日历、订阅和到期提醒。', 'guide': True},
    {'id': 'media_parser', 'module': 'plugins.media_parser', 'label': '链接解析',
     'description': '解析 B站、小红书、Twitter 等链接并发送媒体。', 'guide': True},
    {'id': 'meme', 'module': 'plugins.meme', 'label': '表情包',
     'description': '表情包分类、上传和按场景发送。', 'guide': True},
    {'id': 'meme_learn', 'module': 'plugins.meme_learn', 'label': '表情包学习',
     'description': '视觉模型识别并入库新表情；依赖表情包插件。', 'guide': False},
    {'id': 'memory', 'module': 'plugins.memory', 'label': '长期记忆',
     'description': '用户记忆、候选整理和向量检索。', 'guide': True},
    {'id': 'group_topics', 'module': 'plugins.group_topics', 'label': '群聊话题',
     'description': '群聊关键词和发言统计。', 'guide': True},
    {'id': 'weather', 'module': 'plugins.weather', 'label': '天气提醒',
     'description': '和风天气查询和定时推送。', 'guide': True},
    {'id': 'kb', 'module': 'plugins.kb', 'label': '知识库检索',
     'description': '向模型注入可选的 RAG 知识片段。', 'guide': True},
    {'id': 'token_usage', 'module': 'plugins.token_usage', 'label': 'Token 统计',
     'description': '记录模型 token 用量并按天展示。', 'guide': True},
    {'id': 'gsuid', 'module': 'GenshinUID', 'label': '游戏账号查询（gsuid_core）',
     'description': '加载 NoneBot 侧 GenshinUID 连接器；外部 gsuid_core 进程和其插件不会被删除，需要另行停止。', 'guide': True},
)
_BY_ID = {item['id']: item for item in PLUGINS}
_CORE = frozenset(item['id'] for item in PLUGINS if item.get('core'))
_KNOWN = frozenset(_BY_ID)


def data_path(path=None):
    return Path(path) if path is not None else DEFAULT_PATH


def _defaults():
    return {'onboarded': False, 'disabled': []}


def load(path=None):
    target = data_path(path)
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _defaults()
    if not isinstance(raw, dict):
        return _defaults()
    disabled = raw.get('disabled')
    if not isinstance(disabled, list):
        disabled = []
    # Unknown ids from an older/newer config are ignored until the user saves.
    disabled = sorted({str(value) for value in disabled if str(value) in _KNOWN and str(value) not in _CORE})
    return {'onboarded': raw.get('onboarded') is True, 'disabled': disabled}


def _write(data, path=None):
    target = data_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.plugins-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def view(path=None):
    data = load(path)
    disabled = set(data['disabled'])
    return {
        'onboarded': data['onboarded'],
        'disabled': data['disabled'],
        'plugins': [
            {
                'id': item['id'],
                'module': item['module'],
                'label': item['label'],
                'description': item['description'],
                'core': bool(item.get('core')),
                'guide': bool(item.get('guide')),
                'enabled': item['id'] not in disabled,
            }
            for item in PLUGINS
        ],
        'path': 'data/plugins.json',
    }


def save(disabled=None, onboarded=None, path=None):
    current = load(path)
    if disabled is None:
        disabled = current['disabled']
    if not isinstance(disabled, list):
        raise ValueError('插件开关格式无效')
    clean = sorted({str(value) for value in disabled})
    unknown = sorted(set(clean) - _KNOWN)
    if unknown:
        raise ValueError('未知插件：' + '、'.join(unknown))
    protected = sorted(set(clean) & _CORE)
    if protected:
        raise ValueError('核心插件不能关闭：' + '、'.join(protected))
    data = {
        'onboarded': current['onboarded'] if onboarded is None else bool(onboarded),
        'disabled': clean,
    }
    changed = data != current
    _write(data, path)
    return {**view(path), 'changed': changed, 'restart_required': changed}


def normalize_target(target):
    """Map MuiceBot module names/paths and the gsuid package to catalog ids."""
    if not isinstance(target, str):
        target = str(target)
    value = target.replace('\\', '/').strip()
    value = value.removeprefix('./')
    if value.endswith('.py'):
        value = value[:-3]
    value = value.replace('/', '.')
    if value == 'muicebot' or value.startswith('muicebot.'):
        return 'muicebot'
    if value == 'GenshinUID' or value.startswith('GenshinUID.'):
        return 'gsuid'
    if value.startswith('plugins.'):
        return value.split('.', 1)[1]
    return value.rsplit('.', 1)[-1]


def is_enabled(target, disabled=None, path=None):
    key = normalize_target(target)
    if key == 'muicebot' or key in _CORE:
        return True
    if disabled is None:
        disabled = set(load(path)['disabled'])
    else:
        disabled = {str(value) for value in disabled}
    return key not in disabled


def install_filter(path=None):
    """Patch NoneBot's loader before MuiceBot scans ``plugins/``."""
    import nonebot

    if getattr(nonebot, '_qqbot_plugin_filter', False):
        return
    original = nonebot.load_plugin
    disabled = set(load(path)['disabled'])

    def filtered(name, *args, **kwargs):
        if not is_enabled(name, disabled):
            log.info('plugin disabled by data/plugins.json: %s', normalize_target(name))
            return None
        return original(name, *args, **kwargs)

    nonebot.load_plugin = filtered
    nonebot._qqbot_plugin_filter = True
