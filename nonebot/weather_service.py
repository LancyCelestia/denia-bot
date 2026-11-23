"""QWeather access and persistent, opt-in push configuration."""
import re
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
import httpx

PATH = Path(__file__).resolve().parent / 'data' / 'weather.sqlite3'

@contextmanager
def db(path=PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    os.chmod(path, 0o600)
    c.row_factory = sqlite3.Row
    c.executescript('CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS pushes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, city TEXT NOT NULL, clock TEXT NOT NULL, last_date TEXT NOT NULL DEFAULT "");')
    try:
        with c:
            yield c
    finally:
        c.close()

def validate_host(host):
    host = host.strip().lower().removeprefix('https://').rstrip('/')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.qweatherapi\.com', host):
        raise ValueError('API Host 格式不正确')
    return host

def config(path=PATH):
    with db(path) as c:
        return {r['name']:r['value'] for r in c.execute('SELECT name,value FROM config')}

def save_config(host, key, path=PATH):
    host = validate_host(host)
    with db(path) as c:
        c.execute('INSERT OR REPLACE INTO config VALUES (?,?)', ('host',host))
        if key:
            c.execute('INSERT OR REPLACE INTO config VALUES (?,?)', ('key',key))

def add_push(path, kind, target, city, clock):
    if kind not in ('group','private') or not str(target).isdigit() or not city.strip() or len(city)>40 or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
        raise ValueError('推送配置无效')
    with db(path) as c:
        c.execute('INSERT INTO pushes(kind,target,city,clock) VALUES (?,?,?,?)', (kind,str(target),city.strip(),clock))

def list_pushes(path=PATH):
    with db(path) as c:
        return [dict(r) for r in c.execute('SELECT id,kind,target,city,clock,last_date FROM pushes ORDER BY id')]

def delete_push(id, path=PATH):
    with db(path) as c:
        c.execute('DELETE FROM pushes WHERE id=?', (id,))

def claim_push(id, date, path=PATH):
    with db(path) as c:
        return bool(c.execute('UPDATE pushes SET last_date=? WHERE id=? AND last_date!=?', (date,id,date)).rowcount)

ATTRIBUTION = '数据来源：和风天气 https://developer.qweather.com/attribution.html'
BEIJING = timezone(timedelta(hours=8))
WEEKDAYS = '一二三四五六日'


def _value(node, *path):
    """按路径取和风 v1 的字段；叶子是 {'value': x} 或 {'text': x} 时自动拆开，缺失返回 None。"""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict):
        node = node.get('value', node.get('text'))
    return None if node in (None, '') else node


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else round(number, 1)


def _shown(value):
    return '?' if value is None else value


def _day_label(date_text, today):
    try:
        day = datetime.strptime(date_text, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return date_text or '—', ''
    delta = (day - today).days
    label = {0: '今天', 1: '明天', 2: '后天'}.get(delta, '周' + WEEKDAYS[day.weekday()])
    return label, f'{day.month}/{day.day}'


def _current_facts(x):
    """可选格：接口返回了才显示。字段名按和风 v1 的常见写法兼容几种，取不到就跳过。"""
    facts = []
    humidity = _number(_value(x, 'relativeHumidity')) if _value(x, 'relativeHumidity') is not None else _number(_value(x, 'humidity'))
    if humidity is not None:
        facts.append(('湿度', f'{humidity}%'))
    wind = x.get('wind') if isinstance(x.get('wind'), dict) else {}
    direction = _value(wind, 'direction', 'text') or _value(wind, 'directionText') or _value(wind, 'direction')
    scale = _value(wind, 'scale')
    speed = _number(_value(wind, 'speed'))
    if isinstance(direction, (int, float)):
        direction = None  # 只有角度时不显示，避免出现「270 风」
    if direction or scale is not None or speed is not None:
        part = f'{scale} 级' if scale is not None else (f'{speed} km/h' if speed is not None else '')
        facts.append(('风', ' '.join(p for p in (str(direction or ''), part) if p) or '—'))
    visibility = _number(_value(x, 'visibility'))
    if visibility is not None:
        facts.append(('能见度', f'{visibility} km'))
    return facts[:3]


def _now():
    return datetime.now(BEIJING)


async def fetch_view(host, key, city, forecast=False, client=None, now=None):
    """查询并返回卡片用的结构化视图；文字回退用 `format_text(view)`。"""
    host = validate_host(host)
    if not key: raise ValueError('天气服务尚未配置')
    if not city or len(city)>40: raise ValueError('请提供城市或区县名称')
    own = client is None
    client = client or httpx.AsyncClient(timeout=8, follow_redirects=False)
    try:
        headers={'X-QW-Api-Key':key}
        r = await client.get(f'https://{host}/geo/v2/city/lookup', headers=headers, params={'location':city,'number':5,'lang':'zh'})
        check_response(r)
        locations = r.json().get('location') or []
        if not locations: raise ValueError('没有找到该地点，请提供更完整的城市或区县名')
        loc = locations[0]
        if len(locations)>1 and locations[0].get('name') == locations[1].get('name') and locations[0].get('adm1') != locations[1].get('adm1'):
            raise ValueError('存在同名地点，请加上省份或区县')
        lat, lon = float(loc['lat']), float(loc['lon'])
        if not -90<=lat<=90 or not -180<=lon<=180: raise ValueError('地点坐标无效')
        endpoint = 'daily' if forecast else 'current'
        r = await client.get(f'https://{host}/weather/v1/{endpoint}/{lat:.2f}/{lon:.2f}', headers=headers, params={'lang':'zh','localTime':'true', **({'days':3} if forecast else {})})
        check_response(r)
        data = r.json()
    finally:
        if own: await client.aclose()
    now = (now or _now()).astimezone(BEIJING)
    view = {
        'kind': 'forecast' if forecast else 'now',
        'city': city.strip(),
        'place': ' '.join(dict.fromkeys(x for x in (loc.get('adm1'),loc.get('adm2'),loc.get('name')) if x)),
        'queried_at': f'{now.month}月{now.day}日 周{WEEKDAYS[now.weekday()]} {now:%H:%M}',
        'attribution': ATTRIBUTION,
    }
    if forecast:
        days = []
        for d in data.get('days') or []:
            if not isinstance(d, dict):
                continue
            date = str(d.get('forecastStartTime') or '')[:10]
            label, short = _day_label(date, now.date())
            days.append({
                'date': date, 'label': label, 'short': short,
                'condition': _value(d, 'daytime', 'condition', 'text') or '未知',
                'min': _number(_value(d, 'temperatureMin')),
                'max': _number(_value(d, 'temperatureMax')),
            })
        if not days: raise ValueError('没有可用预报')
        view['days'] = days
    else:
        x = data.get('current', data.get('weather', data))
        x = x if isinstance(x, dict) else {}
        view.update({
            'condition': _value(x, 'condition', 'text') or '未知',
            'temperature': _number(_value(x, 'temperature')),
            'feels_like': _number(_value(x, 'feelsLike')),
            'facts': _current_facts(x),
        })
    return view


def format_text(view):
    """与改版前 `query()` 完全一致的纯文本：图片失败时的回退，也给旧调用方用。"""
    if view['kind'] == 'forecast':
        body = '\n'.join(f"{d['date']} {d['condition']} {_shown(d['min'])}～{_shown(d['max'])}℃" for d in view['days'])
    else:
        body = f"{view['condition']} {_shown(view['temperature'])}℃，体感 {_shown(view['feels_like'])}℃"
    return f"{view['place']}\n{body}\n{view['attribution']}"


async def query(host, key, city, forecast=False, client=None):
    return format_text(await fetch_view(host, key, city, forecast, client))

def check_response(response):
    if response.status_code in (401, 403): raise ValueError('天气服务认证失败或账号无权限，请检查配置')
    if response.status_code == 402: raise ValueError('天气服务账户余额或订阅异常')
    if response.status_code == 429: raise ValueError('天气服务请求过于频繁，请稍后再试')
    response.raise_for_status()
