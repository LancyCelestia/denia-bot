"""从 Grafana 读取 CPU 温度（当前值与窗口平均值）。

只依赖标准库：状态卡和面板都可能调用，不能给它们引入额外依赖。
查询走 Grafana 的 Prometheus 数据源代理：

    GET {url}/api/datasources/proxy/uid/{uid}/api/v1/query_range

取窗口内的样本后，当前值取最后一个时间点的均值，平均值取全部样本均值。
这样不需要 `avg_over_time` 子查询，也不依赖 Grafana 版本。
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SECRET_NAME = 'status_grafana_secrets.json'
MAX_BYTES = 262144
#: 批量查询一次要装下节点 + 访客 + 存储，给到 1 MiB。
BATCH_MAX_BYTES = 1048576
CACHE: dict[tuple[str, str, str, int], tuple[float, dict[str, Any] | None]] = {}
_LAST_ERROR = ''
# 内网 Grafana 不能走环境变量里的代理；显式清空 ProxyHandler。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class GrafanaError(RuntimeError):
    """Grafana 返回了无法处理的内容；消息里带状态和响应片段。"""


def _snippet(raw: bytes) -> str:
    text = raw.decode('utf-8', 'replace').strip().replace('\n', ' ')
    return text[:160] or '空响应'


def _request_json(api: str, token: str, timeout: int) -> Any:
    request = urllib.request.Request(api, headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
    })
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES)
            content_type = response.headers.get('Content-Type', '')
            status = getattr(response, 'status', 200)
    except urllib.error.HTTPError as exc:
        raise GrafanaError(
            f'Grafana HTTP {exc.code}（请求 {api}）：{_snippet(exc.read(MAX_BYTES))}'
        ) from exc
    try:
        return json.loads(raw.decode('utf-8', 'replace'))
    except ValueError as exc:
        hint = ''
        if raw.lstrip().lower().startswith(b'<!doctype html') or b'<html' in raw[:200].lower():
            hint = '；返回的是 Grafana 前端页面，请检查地址是否包含页面路径或启用了 serve_from_sub_path'
        raise GrafanaError(
            'Grafana 返回非 JSON'
            f'（HTTP {status}，{content_type or "无 Content-Type"}，请求 {api}）：{_snippet(raw)}{hint}'
        ) from exc


_UI_MARKERS = ('/d/', '/dashboards', '/explore', '/connections', '/datasources', '/login', '/plugins', '/alerting', '/org')


def _base_url(config: Any) -> str:
    """只保留 Grafana 根地址（可含子路径），丢掉查询串和页面路径。

    常见误填：从浏览器复制的 `/d/xxx`、`?orgId=1`、`/connections/datasources`。
    这些拼上 `/api/...` 后会被 Grafana 的前端路由兜底成 HTML。
    """
    config = config if isinstance(config, dict) else {}
    url = str(config.get('url') or '').strip()
    if not url:
        raise ValueError('缺少 Grafana 地址')
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        raise ValueError('Grafana 地址须以 http:// 或 https:// 开头')
    if not parts.netloc:
        raise ValueError('Grafana 地址缺少主机名')
    if '@' in parts.netloc:
        raise ValueError('Grafana 地址不能包含账号信息')
    path = parts.path.rstrip('/')
    lowered = path.lower()
    for marker in _UI_MARKERS:
        index = lowered.find(marker)
        if index >= 0:
            path = path[:index]
            break
    return f'{parts.scheme}://{parts.netloc}{path}'


def secret_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else Path('.')
    return base / 'data' / SECRET_NAME


def load_secret(root: Path | str | None = None) -> str:
    try:
        value = json.loads(secret_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError, AttributeError):
        return ''
    token = value.get('token') if isinstance(value, dict) else ''
    return str(token or '')


def save_secret(token: str, root: Path | str | None = None) -> None:
    target = secret_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.grafana-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'token': token}, out)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def clear_secret(root: Path | str | None = None) -> None:
    try:
        secret_path(root).unlink()
    except FileNotFoundError:
        pass


def last_error() -> str:
    return _LAST_ERROR


def _set_error(message: str) -> None:
    global _LAST_ERROR
    _LAST_ERROR = message


def _settings(config: Any) -> tuple[str, str, str, int, int]:
    config = config if isinstance(config, dict) else {}
    url = _base_url(config)
    uid = str(config.get('datasource_uid') or '').strip()
    if not uid:
        raise ValueError('缺少 Datasource UID')
    query = str(config.get('temp_query') or '').strip()
    if not query:
        raise ValueError('缺少 PromQL 查询')
    try:
        window = int(config.get('avg_window_minutes') or 60)
    except (TypeError, ValueError):
        window = 60
    window = max(1, min(1440, window))
    try:
        timeout = int(config.get('timeout_seconds') or 3)
    except (TypeError, ValueError):
        timeout = 3
    timeout = max(1, min(10, timeout))
    return url, uid, query, window, timeout


def _values(payload: Any) -> list[tuple[float, float]]:
    data = payload.get('data') if isinstance(payload, dict) else None
    result = data.get('result') if isinstance(data, dict) else None
    rows: list[tuple[float, float]] = []
    for series in result or []:
        if not isinstance(series, dict):
            continue
        for point in series.get('values') or []:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                rows.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
        value = series.get('value')
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                rows.append((float(value[0]), float(value[1])))
            except (TypeError, ValueError):
                pass
    return [(ts, value) for ts, value in rows if math.isfinite(value)]


def _summarize(rows: list[tuple[float, float]], window: int) -> dict[str, Any] | None:
    if not rows:
        return None
    rows.sort()
    last_ts = rows[-1][0]
    latest = [value for ts, value in rows if ts == last_ts]
    current = sum(latest) / len(latest)
    average = sum(value for _, value in rows) / len(rows)
    return {
        'current_c': round(current, 1),
        'avg_c': round(average, 1),
        'window_minutes': window,
        'samples': len(rows),
    }


def _query(url: str, uid: str, query: str, window: int, timeout: int,
           token: str, now: float) -> dict[str, Any] | None:
    params = urllib.parse.urlencode({
        'query': query,
        'start': f'{now - window * 60:.0f}',
        'end': f'{now:.0f}',
        'step': '60',
    })
    api = (
        f'{url}/api/datasources/proxy/uid/'
        f'{urllib.parse.quote(uid, safe="")}/api/v1/query_range?{params}'
    )
    payload = _request_json(api, token, timeout)
    return _summarize(_values(payload), window)


def _datasource(url: str, uid: str, token: str, timeout: int) -> dict[str, Any]:
    payload = _request_json(
        f'{url}/api/datasources/uid/{urllib.parse.quote(uid, safe="")}', token, timeout
    )
    if not isinstance(payload, dict):
        raise GrafanaError('Grafana 数据源信息格式异常')
    return payload


def _query_mode(config: Any, url: str, uid: str, token: str, timeout: int) -> str:
    mode = str(config.get('query_mode') or 'auto').strip().lower()
    if mode in ('prometheus', 'flux'):
        return mode
    datasource = _datasource(url, uid, token, timeout)
    kind = str(datasource.get('type') or '')
    if kind == 'prometheus':
        return 'prometheus'
    if kind == 'influxdb':
        version = str((datasource.get('jsonData') or {}).get('version') or '')
        if version.strip().lower() == 'flux':
            return 'flux'
        raise GrafanaError('InfluxDB 数据源是 InfluxQL 模式，暂不支持；请改用 Flux 数据源')
    raise GrafanaError(f'暂不支持的数据源类型：{kind or "未知"}')


def _query_flux(url: str, uid: str, query: str, window: int, timeout: int,
                token: str, now: float) -> dict[str, Any] | None:
    body = {
        'queries': [{
            'refId': 'A',
            'datasource': {'type': 'influxdb', 'uid': uid},
            # 查询里可用 {window} 占位符，例如 range(start: -{window}m)
            'query': query.replace('{window}', str(window)),
            'rawQuery': True,
            'resultFormat': 'time_series',
            'editorMode': 'code',
        }],
        'from': str(int((now - window * 60) * 1000)),
        'to': str(int(now * 1000)),
    }
    api = f'{url}/api/ds/query'
    request = urllib.request.Request(
        api,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES)
            content_type = response.headers.get('Content-Type', '')
            status = getattr(response, 'status', 200)
    except urllib.error.HTTPError as exc:
        raise GrafanaError(
            f'Grafana HTTP {exc.code}（请求 {api}）：{_snippet(exc.read(MAX_BYTES))}'
        ) from exc
    try:
        payload = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError as exc:
        raise GrafanaError(
            'Grafana 返回非 JSON'
            f'（HTTP {status}，{content_type or "无 Content-Type"}，请求 {api}）：{_snippet(raw)}'
        ) from exc
    results = payload.get('results') if isinstance(payload, dict) else None
    block = results.get('A') if isinstance(results, dict) else None
    if isinstance(block, dict) and block.get('error'):
        raise GrafanaError(f'Grafana 查询失败：{block["error"]}')
    rows: list[tuple[float, float]] = []
    for frame in (block or {}).get('frames') or []:
        fields = [field.get('name') for field in ((frame.get('schema') or {}).get('fields') or [])]
        values = (frame.get('data') or {}).get('values') or []
        if not fields or not values:
            continue
        lowered = [str(name).lower() for name in fields]
        time_index = next((i for i, name in enumerate(lowered) if name in ('_time', 'time')), None)
        value_index = next((i for i, name in enumerate(lowered) if name in ('_value', 'value')), None)
        if value_index is None:
            value_index = next((i for i in range(len(fields)) if i != time_index), None)
        if time_index is None or value_index is None:
            continue
        for index in range(len(values[time_index])):
            try:
                ts = float(values[time_index][index])
                value = float(values[value_index][index])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(value):
                rows.append((ts, value))
    return _summarize(rows, window)


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    """一帧 -> 若干条记录：字段名 + schema 里的标签。

    Grafana 的 Flux 数据源把 group key 放在 `schema.fields[*].labels`，
    字段名只在列名上。丢标签就会把不同节点合并成一条，所以两者都要。
    """
    if not isinstance(frame, dict):
        return []
    schema = frame.get('schema') if isinstance(frame.get('schema'), dict) else {}
    fields = schema.get('fields') if isinstance(schema.get('fields'), list) else []
    names = [
        str(field.get('name') or '') if isinstance(field, dict) else ''
        for field in fields
    ]
    labels: dict[str, Any] = {}
    for field in fields:
        if isinstance(field, dict) and isinstance(field.get('labels'), dict):
            labels.update(field['labels'])  # 后出现的标签覆盖先出现的
    data = frame.get('data') if isinstance(frame.get('data'), dict) else {}
    columns = data.get('values') if isinstance(data.get('values'), list) else []
    if not columns:
        return []
    rows = 0
    for column in columns:
        if isinstance(column, list):
            rows = max(rows, len(column))
    records: list[dict[str, Any]] = []
    for index in range(rows):
        record = dict(labels)
        for position, column in enumerate(columns):
            if position >= len(names) or not names[position]:
                continue
            if isinstance(column, list) and index < len(column):
                # 字段名在标签之后写入：撞名时以实际取值为准。
                record[names[position]] = column[index]
        records.append(record)
    return records


def query_flux_batch(config: dict, queries: dict[str, str], *, token: str = '',
                     root: Path | str | None = None,
                     timeout: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """一次 POST `/api/ds/query`，按 refId 返回展平后的记录。

    与 `fetch_temp` 的区别：这里不聚合时间序列，只把每帧的字段和标签原样
    交给调用方（集群卡片需要在本地按标签分组）。失败抛 `GrafanaError`，
    同时把原因写进 `last_error()`，命令层可以原样回显给人看。
    """
    config = config if isinstance(config, dict) else {}
    try:
        url = _base_url(config)
    except ValueError as exc:
        _set_error(str(exc))
        raise GrafanaError(str(exc)) from exc
    uid = str(config.get('datasource_uid') or '').strip()
    if not uid:
        _set_error('缺少 Datasource UID')
        raise GrafanaError('缺少 Datasource UID')
    batch = {str(ref): str(query or '') for ref, query in (queries or {}).items()}
    if not batch:
        return {}
    if timeout is None:
        try:
            timeout = int(config.get('timeout_seconds') or 5)
        except (TypeError, ValueError):
            timeout = 5
    timeout = max(3, min(15, int(timeout)))
    token = token or load_secret(root)
    if not token:
        _set_error('未配置 Grafana API Key')
        raise GrafanaError('未配置 Grafana API Key')

    body = {
        'queries': [
            {
                'refId': ref,
                'datasource': {'type': 'influxdb', 'uid': uid},
                'query': query,
                'rawQuery': True,
                'resultFormat': 'time_series',
                'editorMode': 'code',
            }
            for ref, query in batch.items()
        ],
        # 集群查询用 Flux 的相对时间窗口，绝对范围留 0 让数据源自己算。
        'from': '0',
        'to': '0',
    }
    api = f'{url}/api/ds/query'
    request = urllib.request.Request(
        api,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(BATCH_MAX_BYTES)
            content_type = response.headers.get('Content-Type', '')
            status = getattr(response, 'status', 200)
    except urllib.error.HTTPError as exc:
        message = f'Grafana HTTP {exc.code}（请求 {api}）：{_snippet(exc.read(BATCH_MAX_BYTES))}'
        _set_error(message)
        raise GrafanaError(message) from exc
    except (urllib.error.URLError, OSError) as exc:
        message = f'Grafana 请求失败（请求 {api}）：{exc}'
        _set_error(message)
        raise GrafanaError(message) from exc
    try:
        payload = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError as exc:
        hint = ''
        if raw.lstrip().lower().startswith(b'<!doctype html') or b'<html' in raw[:200].lower():
            hint = '；返回的是 Grafana 前端页面，请检查地址是否包含页面路径或启用了 serve_from_sub_path'
        message = (
            'Grafana 返回非 JSON'
            f'（HTTP {status}，{content_type or "无 Content-Type"}，请求 {api}）：'
            f'{_snippet(raw)}{hint}'
        )
        _set_error(message)
        raise GrafanaError(message) from exc
    results = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        _set_error('Grafana 返回结果格式异常：缺少 results')
        raise GrafanaError('Grafana 返回结果格式异常：缺少 results')
    collected: dict[str, list[dict[str, Any]]] = {}
    for ref, block in results.items():
        if not isinstance(block, dict):
            collected[str(ref)] = []
            continue
        if block.get('error'):
            message = f'Grafana 查询失败（{ref}）：{block["error"]}'
            _set_error(message)
            raise GrafanaError(message)
        records: list[dict[str, Any]] = []
        for frame in block.get('frames') or []:
            records.extend(_frame_records(frame))
        collected[str(ref)] = records
    _set_error('')
    return {ref: collected.get(ref, []) for ref in batch}


def fetch_temp(config: Any, token: str = '', *, root: Path | str | None = None,
               now: float | None = None, use_cache: bool = True) -> dict[str, Any] | None:
    """返回 `{current_c, avg_c, window_minutes, samples}`；失败返回 None。"""
    global _LAST_ERROR
    config = config if isinstance(config, dict) else {}
    if not config.get('enabled'):
        return None
    try:
        url, uid, query, window, timeout = _settings(config)
    except ValueError as exc:
        _set_error(str(exc))
        return None
    token = token or load_secret(root)
    if not token:
        _set_error('未配置 Grafana API Key')
        return None
    key = (url, uid, query, window)
    now = time.time() if now is None else float(now)
    try:
        ttl = int(config.get('cache_seconds') or 60)
    except (TypeError, ValueError):
        ttl = 60
    ttl = max(0, min(600, ttl))
    if use_cache and ttl:
        hit = CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        mode = _query_mode(config, url, uid, token, timeout)
        if mode == 'flux':
            result = _query_flux(url, uid, query, window, timeout, token, now)
        else:
            result = _query(url, uid, query, window, timeout, token, now)
    except Exception as exc:  # noqa: BLE001 - 网络/解析错误都只降级，不打断状态卡
        _set_error(f'{type(exc).__name__}: {exc}')
        return None
    _LAST_ERROR = ''
    CACHE[key] = (now, result)
    return result


def fetch_datasources(config: Any, token: str = '',
                      root: Path | str | None = None) -> list[dict[str, Any]] | None:
    """列出 Grafana 数据源，供面板自动填入 Prometheus 的 UID。"""
    global _LAST_ERROR
    config = config if isinstance(config, dict) else {}
    try:
        url = _base_url(config)
        timeout = int(config.get('timeout_seconds') or 3)
    except (TypeError, ValueError) as exc:
        _set_error(str(exc))
        return None
    token = token or load_secret(root)
    if not token:
        _set_error('未配置 Grafana API Key')
        return None
    try:
        payload = _request_json(
            f'{url}/api/datasources', token, max(1, min(10, timeout))
        )
    except Exception as exc:  # noqa: BLE001 - 面板需要看到失败原因
        _set_error(f'{type(exc).__name__}: {exc}')
        return None
    if not isinstance(payload, list):
        _set_error('Grafana 数据源列表格式异常')
        return None
    _LAST_ERROR = ''
    items = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        items.append({
            'uid': str(row.get('uid') or ''),
            'name': str(row.get('name') or ''),
            'type': str(row.get('type') or ''),
            'default': bool(row.get('isDefault')),
        })
    return items


def panel_view(config: Any, root: Path | str | None = None) -> dict[str, Any]:
    """面板展示用：配置字段 + 是否已保存密钥（不回显密钥）。"""
    config = config if isinstance(config, dict) else {}
    return {
        'enabled': bool(config.get('enabled')),
        'url': str(config.get('url') or ''),
        'datasource_uid': str(config.get('datasource_uid') or ''),
        'temp_query': str(config.get('temp_query') or ''),
        'avg_window_minutes': config.get('avg_window_minutes') or 60,
        'configured': bool(load_secret(root)),
        'error': last_error(),
    }
