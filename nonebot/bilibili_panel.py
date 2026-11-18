"""管理面板的 B站动态订阅只读视图。

机器人进程独占 `data/bilibili_dynamic/state.sqlite3` 的写锁；面板只以只读 URI
打开同一份文件做一次快照查询，不写库、不抓取、不投递。增删订阅仍然只在
群聊 / 私聊里用 `.b站订阅` / `.b站退订`。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

SCHEMA_VERSION = 31
DEFAULT_STATE = Path('data') / 'bilibili_dynamic' / 'state.sqlite3'
STATUSES = ('pending', 'retry', 'sending', 'unknown', 'sent', 'failed', 'skipped')
_FALSE = {'0', 'false', 'off', 'no', 'n', 'f', '关', '否'}
_ID = re.compile(r'[1-9][0-9]{0,19}')
ERROR_LABELS = {
    'REMOTE_RESTRICTED': 'B站访问受限，来源已暂停',
    'REMOTE_AUTH_REQUIRED': 'B站登录态缺失或失效',
    'REMOTE_HTTP': 'B站请求失败',
    'REMOTE_SCHEMA': 'B站返回格式异常',
    'PUBLIC_UNPROVEN': '动态公开性未确认，未推送',
    'DEADLINE': '检查超时',
    'STORAGE_FAULT': '本地存储异常，已停止投递',
    'CAPACITY': '订阅或待发容量已满',
    'SCAN_GAP': '动态检查不完整，可能遗漏',
}


def _text(value, limit):
    """库里的一切字符串都当纯文本：去控制符、截断，前端只用 textContent。"""
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if c == '\n' or not unicodedata.category(c).startswith('C'))[:limit]


def _env_file(path):
    """按 dotenv 习惯读 `.env` 的顶层 KEY=value，仅供面板展示，解析失败就当没写。"""
    values = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, _, raw = stripped.partition('=')
        key = key.strip()
        if key.startswith('export '):
            key = key[len('export '):].strip()
        if not key or any(c.isspace() for c in key):
            continue
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '"\'':
            raw = raw[1:-1]
        values.setdefault(key, raw)
    return values


def _setting(file_values):
    """真环境变量盖过 `.env`，与 NoneBot 的 dotenv 读取顺序一致。"""
    def get(name, default=''):
        value = os.environ.get(name)
        if value not in (None, ''):
            return value
        value = file_values.get(name)
        return default if value in (None, '') else value
    return get


def _bool(raw):
    if not raw:
        return None
    return raw.strip().lower() not in _FALSE


def _interval(raw):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 60 <= value <= 86400 else None


def _targets(raw):
    """`BILI_TARGETS` 是 JSON 数组；解析不了返回 None，不猜。"""
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(values, list):
        return None
    parsed = set()
    for value in values:
        parts = value.split(':') if isinstance(value, str) else ()
        if (len(parts) == 3 and parts[1] in {'group', 'private'}
                and _ID.fullmatch(parts[0]) and _ID.fullmatch(parts[2])):
            parsed.add(tuple(parts))
    return parsed


def state_path(root):
    """与插件配置同源：BILI_STATE_FILE 优先，否则用插件默认路径。"""
    file_values = _env_file(Path(root) / '.env')
    raw = _setting(file_values)('BILI_STATE_FILE')
    path = Path(raw) if raw else DEFAULT_STATE
    return path if path.is_absolute() else Path(root) / path


def _read(path):
    """一次只读快照；缺库、版本不符、读不出来都明确报出来，不静默兜底。"""
    if not path.exists():
        return {'available': False, 'reason': '未找到 B站动态订阅状态库：插件可能未启用或还没有启动过。'}
    try:
        db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=2)
    except sqlite3.Error as exc:
        log.warning('bilibili panel open failed: %s', type(exc).__name__)
        raise HTTPException(503, '订阅状态库暂不可读，请稍后刷新或检查机器人日志。') from exc
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 0:
            raise HTTPException(503, '订阅状态库尚未初始化：插件可能刚启动或初始化失败。')
        if version != SCHEMA_VERSION:
            raise HTTPException(503, f'订阅状态库版本 {version} 与面板不一致（面板支持 {SCHEMA_VERSION}），请同步升级。')
        db.execute('BEGIN')
        stop = db.execute("SELECT value FROM meta WHERE key='source_stop'").fetchone()
        source_stopped = bool(stop) and str(stop[0]) == '1'
        subs = [dict(row) for row in db.execute(
            'SELECT id,bot,kind,target,uid,name,baseline,last_check,last_error,gap '
            'FROM subscriptions ORDER BY bot,kind,target,id')]
        counts = {}
        for row in db.execute("SELECT sub_id,status,count(*) AS n FROM deliveries "
                              "WHERE status!='baseline' GROUP BY sub_id,status"):
            counts.setdefault(row['sub_id'], {})[row['status']] = row['n']
    except sqlite3.Error as exc:
        log.warning('bilibili panel read failed: %s', type(exc).__name__)
        raise HTTPException(503, '订阅状态库暂不可读，请稍后刷新或检查机器人日志。') from exc
    finally:
        db.close()

    sessions = {}
    for sub in subs:
        key = (sub['bot'], sub['kind'], sub['target'])
        session = sessions.setdefault(key, {
            'bot': sub['bot'], 'kind': sub['kind'], 'target': sub['target'],
            'subscriptions': [], 'deliveries': {status: 0 for status in STATUSES}})
        deliveries = {status: counts.get(sub['id'], {}).get(status, 0) for status in STATUSES}
        for status, count in deliveries.items():
            session['deliveries'][status] += count
        code = sub['last_error'] if isinstance(sub['last_error'], str) else ''
        session['subscriptions'].append({
            'uid': sub['uid'], 'name': _text(sub['name'], 80),
            'subscribed_at': sub['baseline'], 'last_check': sub['last_check'],
            'error_code': _text(code, 40), 'error': ERROR_LABELS.get(code, ''),
            'gap': bool(sub['gap']), 'deliveries': deliveries})

    totals = {
        'sessions': len(sessions), 'subscriptions': len(subs), 'uids': len({s['uid'] for s in subs}),
        'deliveries': {status: sum(s['deliveries'][status] for s in sessions.values()) for status in STATUSES}}
    return {'available': True, 'source_stopped': source_stopped,
            'checked_at': max((s['last_check'] for s in subs), default=0),
            'totals': totals, 'sessions': list(sessions.values())}


def view(root):
    """面板视图：状态库快照 + `.env` 里的开关与接收目标，用于指出「为什么没在投递」。"""
    root = Path(root)
    file_values = _env_file(root / '.env')
    get = _setting(file_values)
    enabled = _bool(get('BILI_ENABLED'))
    targets = _targets(get('BILI_TARGETS'))
    data = _read(state_path(root))
    data['config'] = {'enabled': enabled, 'interval': _interval(get('BILI_INTERVAL'))}
    for session in data.get('sessions', ()):
        key = (session['bot'], session['kind'], session['target'])
        session['target_allowed'] = None if targets is None else key in targets
    return data


def register(app, *, authorized, root):
    @app.get('/api/bilibili')
    def bilibili_state(req: Request):
        authorized(req)
        return view(root)
