# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""Proxmox VE 集群状态：固定 Flux 查询、按标签展平、视图装配。

数据只从现有 Grafana 的 InfluxDB（Flux）数据源读，**不直连 PVE API**，
也不新增密钥：地址、超时和 token 全部沿用 `status_grafana`。

为什么查询语句写死：Telegraf 的 `proxmox` 输入插件字段名是稳定的，但允许
用户编辑整段 Flux 只会把注入和拼错的风险交给面板。面板能改的只有 bucket、
时间窗口、节点筛选和温度开关这几项。

这一层不 import `nonebot` / `psutil`，所以测试里没有机器人也没有真实集群。
约定：`None` 表示「没取到这个值」，绝不伪造成 0；渲染层据此显示 `--`。
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Sequence

import status_grafana
import status_logic

#: Bucket 会被拼进 Flux 字符串，所以只接受安全字符集。
BUCKET_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
EMPTY = '--'
GUEST_STATUS_LABELS: dict[str, tuple[str, str]] = {
    'running': ('运行中', 'good'),
    'stopped': ('已停止', 'warn'),
    'paused': ('已暂停', 'warn'),
}
GUEST_TYPE_LABELS = {'qemu': 'VM', 'lxc': 'LXC'}
TEMP_LABELS = ('Tctl', 'Package id 0')
TEMP_CHIPS = ('k10temp', 'coretemp')

#: 重复查询的短缓存；面板「测试查询」会绕开它。
CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _ratio(used: Any, total: Any) -> float | None:
    used_value = _number(used)
    total_value = _number(total)
    if used_value is None or total_value is None or total_value <= 0:
        return None
    return used_value / total_value


def _percent(ratio: Any) -> float | None:
    """0～1 的比例换成百分数；负数（Telegraf 偶尔给 -1）也算缺失。"""
    value = _number(ratio)
    if value is None or value < 0:
        return None
    return round(value * 100, 1)


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    number = _number(value)
    if number is None:
        return None
    return number != 0


def _text(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


def _records(results: Any, ref: str):
    """逐个吐 refId 下的记录；非 dict 的行直接跳过。"""
    block = results.get(ref) if isinstance(results, dict) else None
    for item in block if isinstance(block, list) else ():
        if isinstance(item, dict):
            yield item


def parse_node_filter(value: Any) -> list[str]:
    """面板可能提交逗号/空白分隔的字符串，也可能提交列表。"""
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = re.split(r'[,\s]+', str(value or ''))
    return [item.strip() for item in items if item.strip()]


def _flux(bucket: str, window: int, measurement: str, *,
          object_filter: str = '', fields: Sequence[str] = ()) -> str:
    parts = [
        f'from(bucket: "{bucket}")',
        f'|> range(start: -{window}m)',
        f'|> filter(fn: (r) => r["_measurement"] == "{measurement}")',
    ]
    if object_filter:
        parts.append(f'|> filter(fn: (r) => {object_filter})')
    if fields:
        condition = ' or '.join(f'r["_field"] == "{field}"' for field in fields)
        parts.append(f'|> filter(fn: (r) => {condition})')
    parts.append('|> last()')
    return ' '.join(parts)


def build_queries(bucket: str, window_minutes: int, *,
                  include_temperature: bool = True) -> dict[str, str]:
    """生成 A～F 六条 Flux；温度查询按开关决定是否追加。

    `window_minutes` 夹在 1～1440 分钟：窗口过小会查不到采样，过大则
    把 Grafana 的内存打满。
    """
    name = str(bucket or '').strip()
    if not BUCKET_RE.fullmatch(name):
        raise status_grafana.GrafanaError(
            f'bucket 名称非法：{name or "空"}（只允许 A-Z a-z 0-9 _ . -，最长 64 字符）'
        )
    try:
        window = int(window_minutes)
    except (TypeError, ValueError):
        window = 10
    window = max(1, min(1440, window))
    queries = {
        'A': _flux(name, window, 'cpustat', object_filter='r["object"] == "nodes"',
                   fields=('cpu', 'cpus', 'avg1', 'avg5', 'avg15')),
        'B': _flux(name, window, 'memory', object_filter='r["object"] == "nodes"',
                   fields=('memused', 'memtotal', 'swapused', 'swaptotal')),
        'C': _flux(name, window, 'system', object_filter='r["object"] == "nodes"',
                   fields=('uptime',)),
        'D': _flux(name, window, 'system',
                   object_filter='(r["object"] == "qemu" or r["object"] == "lxc")',
                   fields=('name', 'status', 'cpu', 'cpus', 'mem', 'maxmem', 'uptime')),
        'E': _flux(name, window, 'system', object_filter='r["object"] == "storages"',
                   fields=('used', 'total', 'avail', 'content', 'active')),
    }
    if include_temperature:
        # 不按 chip 过滤：k10temp 之外的芯片也收进来，归一化时再挑 CPU 封装。
        queries['F'] = _flux(name, window, 'hwmon', fields=('temp_celsius',))
    return queries


def _temp_priority(record: dict[str, Any]) -> int:
    if str(record.get('label') or '') in TEMP_LABELS:
        return 2
    if str(record.get('chip_name') or '').lower() in TEMP_CHIPS:
        return 1
    return 0


def normalize(results: dict[str, list[dict]], *,
              node_filter: Sequence[str] = ()) -> dict[str, list[dict[str, Any]]]:
    """把 Grafana 的 records 按标签合并成节点 / 访客 / 存储三类原始记录。

    Grafana 的 Flux 数据源按 group key 分 frame，Telegraf 又把 `_field`
    放进 group key，所以同一台节点的 `cpu` 和 `cpus` 是两条独立记录，
    必须按 `host` 合并后才有意义。
    """
    nodes: dict[str, dict[str, Any]] = {}
    guests: dict[tuple[str, int], dict[str, Any]] = {}
    storages: dict[tuple[str, str], dict[str, Any]] = {}
    temp_pick: dict[str, tuple[int, float]] = {}

    def node_entry(host: str) -> dict[str, Any]:
        return nodes.setdefault(host, {
            'name': host,
            'cpu_ratio': None,
            'cores': None,
            'load': {'avg1': None, 'avg5': None, 'avg15': None},
            'memory_used': None,
            'memory_total': None,
            'memory_ratio': None,
            'swap_used': None,
            'swap_total': None,
            'swap_ratio': None,
            'uptime': None,
            'temperature_c': None,
        })

    for record in _records(results, 'A'):
        host = _text(record.get('host'))
        if not host:
            continue
        entry = node_entry(host)
        if 'cpu' in record:
            entry['cpu_ratio'] = _number(record['cpu'])
        if 'cpus' in record:
            entry['cores'] = _count(record['cpus'])
        for key in ('avg1', 'avg5', 'avg15'):
            if key in record:
                entry['load'][key] = _number(record[key])

    for record in _records(results, 'B'):
        host = _text(record.get('host'))
        if not host:
            continue
        entry = node_entry(host)
        if 'memused' in record:
            entry['memory_used'] = _number(record['memused'])
        if 'memtotal' in record:
            entry['memory_total'] = _number(record['memtotal'])
        if 'swapused' in record:
            entry['swap_used'] = _number(record['swapused'])
        if 'swaptotal' in record:
            entry['swap_total'] = _number(record['swaptotal'])

    for record in _records(results, 'C'):
        host = _text(record.get('host'))
        if not host:
            continue
        if 'uptime' in record:
            node_entry(host)['uptime'] = _number(record['uptime'])

    for record in _records(results, 'F'):
        host = _text(record.get('host'))
        if not host:
            continue
        value = _number(record.get('temp_celsius'))
        if value is None:
            continue
        priority = _temp_priority(record)
        if host not in temp_pick or priority > temp_pick[host][0]:
            temp_pick[host] = (priority, value)
    for host, (_, value) in temp_pick.items():
        node_entry(host)['temperature_c'] = value

    for entry in nodes.values():
        entry['memory_ratio'] = _ratio(entry['memory_used'], entry['memory_total'])
        entry['swap_ratio'] = _ratio(entry['swap_used'], entry['swap_total'])

    for record in _records(results, 'D'):
        guest_type = _text(record.get('object'))
        if guest_type not in GUEST_TYPE_LABELS:
            continue
        vmid = _count(record.get('vmid'))
        if vmid is None:
            continue
        entry = guests.setdefault((guest_type, vmid), {
            'vmid': vmid,
            'name': None,
            'node': None,
            'type': guest_type,
            'status': None,
            'cpu_ratio': None,
            'cores': None,
            'mem_used': None,
            'mem_total': None,
            'uptime': None,
        })
        name = _text(record.get('name')) or _text(record.get('host'))
        if name and not entry['name']:
            entry['name'] = name
        node = _text(record.get('nodename'))
        if node:
            entry['node'] = node
        if 'status' in record:
            entry['status'] = _text(record['status'])
        if 'cpu' in record:
            entry['cpu_ratio'] = _number(record['cpu'])
        if 'cpus' in record:
            entry['cores'] = _count(record['cpus'])
        if 'mem' in record:
            entry['mem_used'] = _number(record['mem'])
        if 'maxmem' in record:
            entry['mem_total'] = _number(record['maxmem'])
        if 'uptime' in record:
            entry['uptime'] = _number(record['uptime'])

    for record in _records(results, 'E'):
        name = _text(record.get('host'))
        if not name:
            continue
        node = _text(record.get('nodename')) or ''
        entry = storages.setdefault((node, name), {
            'name': name,
            'node': node or None,
            'type': None,
            'content': None,
            'used': None,
            'total': None,
            'avail': None,
            'ratio': None,
            'active': None,
        })
        if 'type' in record:
            entry['type'] = _text(record['type'])
        if 'content' in record:
            entry['content'] = _text(record['content'])
        if 'used' in record:
            entry['used'] = _number(record['used'])
        if 'total' in record:
            entry['total'] = _number(record['total'])
        if 'avail' in record:
            entry['avail'] = _number(record['avail'])
        if 'active' in record:
            entry['active'] = _flag(record['active'])

    for entry in storages.values():
        entry['ratio'] = _ratio(entry['used'], entry['total'])

    wanted = set(parse_node_filter(node_filter))
    node_rows = sorted(nodes.values(), key=lambda item: item['name'].lower())
    guest_rows = sorted(
        guests.values(),
        key=lambda item: (
            item['node'] or '',
            0 if item['type'] == 'qemu' else 1,
            item['vmid'],
        ),
    )
    storage_rows = sorted(
        storages.values(), key=lambda item: (item['node'] or '', item['name'].lower())
    )
    if wanted:
        node_rows = [item for item in node_rows if item['name'] in wanted]
        guest_rows = [item for item in guest_rows if (item['node'] or '') in wanted]
        storage_rows = [item for item in storage_rows if (item['node'] or '') in wanted]
    return {'nodes': node_rows, 'guests': guest_rows, 'storages': storage_rows}


def _load_text(load: Any) -> str:
    values = load if isinstance(load, dict) else {}
    numbers = [_number(values.get(key)) for key in ('avg1', 'avg5', 'avg15')]
    if all(value is None for value in numbers):
        return EMPTY
    return ' / '.join(f'{value:.2f}' if value is not None else EMPTY for value in numbers)


def _pair_text(used: Any, total: Any) -> str:
    used_value = _number(used)
    total_value = _number(total)
    if used_value is None or total_value is None or total_value <= 0:
        return EMPTY
    return f'{status_logic.format_bytes(used_value)} / {status_logic.format_bytes(total_value)}'


def _node_view(entry: dict[str, Any]) -> dict[str, Any]:
    cpu_percent = _percent(entry.get('cpu_ratio'))
    memory_percent = _percent(entry.get('memory_ratio'))
    swap_percent = _percent(entry.get('swap_ratio'))
    temperature = _number(entry.get('temperature_c'))
    temperature_text = f'{temperature:.1f}℃' if temperature is not None else EMPTY
    complete = cpu_percent is not None and memory_percent is not None
    return {
        'name': str(entry.get('name') or EMPTY),
        'cpu_percent': cpu_percent,
        'cpu_text': status_logic.format_pct(cpu_percent),
        'cpu_level': status_logic.percent_level(cpu_percent),
        'cpu_note': temperature_text if temperature is not None else '',
        'cores': entry.get('cores') if isinstance(entry.get('cores'), int) else None,
        'load': _load_text(entry.get('load')),
        'memory_percent': memory_percent,
        'memory_text': _pair_text(entry.get('memory_used'), entry.get('memory_total')),
        'memory_level': status_logic.percent_level(memory_percent),
        'swap_percent': swap_percent,
        'swap_text': _pair_text(entry.get('swap_used'), entry.get('swap_total')),
        'swap_level': status_logic.percent_level(swap_percent),
        'uptime': status_logic.format_duration(entry.get('uptime')),
        'temperature_text': temperature_text,
        'status': '正常' if complete else '部分数据缺失',
        'status_level': 'good' if complete else 'warn',
    }


def _guest_view(entry: dict[str, Any]) -> dict[str, Any]:
    guest_type = str(entry.get('type') or '')
    status_raw = str(entry.get('status') or '').strip()
    status_label, status_level = GUEST_STATUS_LABELS.get(
        status_raw, (status_raw or EMPTY, 'warn')
    )
    cpu_percent = _percent(entry.get('cpu_ratio'))
    memory_ratio = _ratio(entry.get('mem_used'), entry.get('mem_total'))
    return {
        'vmid': entry.get('vmid'),
        'name': str(entry.get('name') or EMPTY),
        'node': str(entry.get('node') or EMPTY),
        'type': guest_type or EMPTY,
        'type_label': GUEST_TYPE_LABELS.get(
            guest_type, guest_type.upper() or EMPTY
        ),
        'status': status_raw or EMPTY,
        'status_label': status_label,
        'status_level': status_level,
        'cpu_percent': cpu_percent,
        'cpu_text': status_logic.format_pct(cpu_percent),
        'cores': entry.get('cores') if isinstance(entry.get('cores'), int) else None,
        'mem_text': _pair_text(entry.get('mem_used'), entry.get('mem_total')),
        'mem_percent': _percent(memory_ratio),
        'uptime': status_logic.format_duration(entry.get('uptime')),
    }


def _storage_view(entry: dict[str, Any]) -> dict[str, Any]:
    percent = _percent(entry.get('ratio'))
    active = entry.get('active')
    return {
        'name': str(entry.get('name') or EMPTY),
        'node': str(entry.get('node') or EMPTY),
        'type': str(entry.get('type') or EMPTY),
        'content': str(entry.get('content') or EMPTY),
        'used_text': status_logic.format_bytes(entry.get('used')),
        'total_text': status_logic.format_bytes(entry.get('total')),
        'percent': percent,
        'percent_text': status_logic.format_pct(percent),
        'level': status_logic.percent_level(percent),
        'active': active if isinstance(active, bool) else None,
    }


def build_view(raw: dict, *, title: str = 'Proxmox VE', source: str = 'Grafana',
               now: float | None = None) -> dict[str, Any]:
    """把归一化后的原始数字装成渲染器的输入。

    百分比、字节、时长都在这里定稿，渲染层不再做单位换算；缺失值一律给
    `--`，避免把「没查到」画成「0%」。
    """
    raw = raw if isinstance(raw, dict) else {}
    now = time.time() if now is None else float(now)
    nodes = [_node_view(item) for item in raw.get('nodes') or [] if isinstance(item, dict)]
    guest_raw = [item for item in raw.get('guests') or [] if isinstance(item, dict)]
    guests = [_guest_view(item) for item in guest_raw]
    storages = [
        _storage_view(item) for item in raw.get('storages') or [] if isinstance(item, dict)
    ]
    nodes.sort(key=lambda item: item['name'].lower())
    guests.sort(key=lambda item: (
        item['node'],
        0 if item['type'] == 'qemu' else 1,
        item['vmid'] if isinstance(item['vmid'], int) else 0,
    ))
    storages.sort(key=lambda item: (item['node'], item['name'].lower()))
    running = sum(1 for item in guest_raw if str(item.get('status') or '') == 'running')
    return {
        'title': str(title or 'Proxmox VE'),
        'generated_at': status_logic.format_clock(now),
        'source': str(source or 'Grafana'),
        'summary': {
            'nodes': len(nodes),
            'guests': len(guests),
            'running': running,
            'vms': sum(1 for item in guest_raw if item.get('type') == 'qemu'),
            'lxcs': sum(1 for item in guest_raw if item.get('type') == 'lxc'),
            'storages': len(storages),
        },
        'nodes': nodes,
        'guests': guests,
        'storages': storages,
    }


def fetch(cluster_config: dict, grafana_config: dict, *, root=None, token: str = '',
          now: float | None = None, use_cache: bool = True) -> dict[str, Any]:
    """查询 Grafana 并返回视图；没有取到任何数据时抛 `GrafanaError`。

    `cluster_config.datasource_uid` 非空时覆盖 `grafana.datasource_uid`，
    便于同一台 Grafana 上切换 bucket 所在的数据源。
    """
    cluster = cluster_config if isinstance(cluster_config, dict) else {}
    grafana = dict(grafana_config) if isinstance(grafana_config, dict) else {}
    now = time.time() if now is None else float(now)

    bucket = str(cluster.get('bucket') or 'proxmox').strip() or 'proxmox'
    raw_window = cluster.get('window_minutes')
    try:
        window = 10 if raw_window is None or str(raw_window).strip() == '' else int(raw_window)
    except (TypeError, ValueError):
        window = 10
    window = max(1, min(1440, window))
    include_temperature = bool(cluster.get('show_temperature', True))
    node_filter = parse_node_filter(cluster.get('node_filter'))
    override = str(cluster.get('datasource_uid') or '').strip()
    if override:
        grafana['datasource_uid'] = override

    try:
        ttl = int(cluster.get('cache_seconds') or 0)
    except (TypeError, ValueError):
        ttl = 0
    ttl = max(0, min(600, ttl))
    key = (
        str(grafana.get('url') or '').strip(),
        str(grafana.get('datasource_uid') or '').strip(),
        bucket,
        window,
        tuple(node_filter),
        include_temperature,
    )
    if use_cache and ttl:
        hit = CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]

    queries = build_queries(bucket, window, include_temperature=include_temperature)
    results = status_grafana.query_flux_batch(grafana, queries, token=token, root=root)
    raw = normalize(results, node_filter=node_filter)
    if not (raw['nodes'] or raw['guests'] or raw['storages']):
        message = 'Grafana 查询没有返回节点/访客/存储数据'
        status_grafana._set_error(message)
        raise status_grafana.GrafanaError(message)
    view = build_view(
        raw,
        title=str(cluster.get('title') or 'Proxmox VE').strip() or 'Proxmox VE',
        source='Grafana',
        now=now,
    )
    if ttl:
        CACHE[key] = (now, view)
    return view


def clear_cache() -> None:
    CACHE.clear()


def summary_text(view: dict) -> str:
    """图发不出去时的文字兜底：节点资源、访客数量、占用最高的几个存储。"""
    view = view if isinstance(view, dict) else {}
    summary = view.get('summary') if isinstance(view.get('summary'), dict) else {}
    nodes = [item for item in view.get('nodes') or [] if isinstance(item, dict)]
    guests = view.get('guests') or []
    storages = [item for item in view.get('storages') or [] if isinstance(item, dict)]
    title = str(view.get('title') or 'Proxmox VE')
    lines = [
        f'{title}：节点 {summary.get("nodes", len(nodes))} · '
        f'访客 {summary.get("guests", len(guests))}'
        f'（运行 {summary.get("running", 0)}）· 存储 {summary.get("storages", len(storages))}'
    ]
    for node in nodes[:3]:
        lines.append(
            f'{node.get("name") or EMPTY}：CPU {node.get("cpu_text") or EMPTY}'
            f' · 内存 {node.get("memory_text") or EMPTY}'
        )
    if guests:
        lines.append(f'访客：VM {summary.get("vms", 0)} · LXC {summary.get("lxcs", 0)}')
    ranked = sorted(
        storages,
        key=lambda item: (
            item.get('percent') if isinstance(item.get('percent'), (int, float)) else -1
        ),
        reverse=True,
    )
    if ranked:
        lines.append('存储：' + ' · '.join(
            f'{item.get("name") or EMPTY} {item.get("percent_text") or EMPTY}'
            for item in ranked[:3]
        ))
    return '\n'.join(lines)
