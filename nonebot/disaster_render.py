# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""远程 Playwright 渲染客户端 + B Bulletin 灾害卡片 HTML。

用户已选 B Bulletin：900 宽浅色公告，左色轨、官方抬头、印章、关键数字、双栏字段、正文与来源。
2026-11 改版：
- 印章与标题同一行排版，不再绝对定位压住字段表；
- 去掉与数据无关的示意图（固定坐标的「震中」「台风路径」），改为关键数字；
  台风有当前与预报坐标时，按经纬度比例画出真实的移动方向与距离；
- 字号按 900 宽重排（最小 16px），强调色文字统一满足 4.5:1；
- 地震、台风、海啸卡走 /render-card，高度随内容，长文本不再被固定 1200 高度悄悄裁掉。
气象预警卡（disaster_weather_card）保持原样。

卡片自包含（内联 CSS/SVG，无外部图片/字体链接）；渲染端默认禁 JS、拦外网。
"""
from __future__ import annotations

import html
import math
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

import bot_identity
import disaster_china_logic as china
import disaster_weather_card as weather_card
import disaster_weather_map as weather_map_mod
from disaster_logic import EarthquakeEvent, SOURCE_LABELS, distance_km, event_lead, local_distance


class RenderError(RuntimeError):
    """渲染服务不可用、返回非图片或 HTTP 错误。"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def _time(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '—'
    text = text.replace('/', '-')
    return text if '北京时间' in text else text + '（北京时间）'


def _fact(label: str, value: Any) -> str:
    return (
        '<div class="fact"><span class="fact-label">'
        f'{_e(label)}</span><b class="fact-value">{_e(value or "—")}</b></div>'
    )


def _facts(rows: list[tuple[str, Any]]) -> str:
    # 双栏网格：奇数个字段时最后一格占满一行，底边线才不会断在半空。
    cells = [_fact(label, value) for label, value in rows]
    if len(cells) % 2:
        cells[-1] = cells[-1].replace('<div class="fact">', '<div class="fact wide">', 1)
    return ''.join(cells)


def _weather_accent(color: str) -> str:
    return {
        '红色': '#b83b2f', '橙色': '#c2711f', '黄色': '#b68a1e',
        '蓝色': '#2f6f9f', '白色': '#7b7b7b',
    }.get(color, '#b68a1e')


def _typhoon_accent(level: str) -> str:
    if '超强' in level:
        return '#b83b2f'
    if '强' in level:
        return '#c2711f'
    if '热带风暴' in level:
        return '#2f6f9f'
    return '#b68a1e'


def _earthquake_accent(event: EarthquakeEvent) -> str:
    magnitude = event.magnitude or 0
    if magnitude >= 6:
        return '#b83b2f'
    if magnitude >= 5:
        return '#c2711f'
    return '#3d7d5f'


def _weather_region(alert: china.WeatherAlert) -> str:
    return ' · '.join(part for part in (alert.province, alert.city, alert.district) if part)


# 强调色同时画色轨和文字；黄、橙、灰在米色底上达不到 4.5:1，文字改用同色系深一档。
_INK = {'#b68a1e': '#86660f', '#c2711f': '#9e5814', '#7b7b7b': '#5f5f5f'}


def _ink(accent: str) -> str:
    return _INK.get(accent, accent)


BULLETIN_STYLE = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:#f3efe4;color:#1b1b18;font-family:"Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.card{width:900px;display:flex;background:#f3efe4;color:#1b1b18;font-family:"Noto Serif CJK SC","Songti SC","Microsoft YaHei",serif}
.rail{width:16px;flex:0 0 16px;background:var(--accent)}
.sheet{flex:1;min-width:0;padding:40px 52px 34px}
.mast{display:flex;justify-content:space-between;gap:16px;font-size:18px;letter-spacing:.06em;color:#5f594f;border-bottom:2px solid #1b1b18;padding-bottom:12px}
.mast span:last-child{flex-shrink:0}
.head{display:flex;align-items:flex-start;gap:28px;margin:22px 0 22px}
.title{flex:1;min-width:0;font-size:46px;line-height:1.25;font-weight:750;margin:0;letter-spacing:-.01em;overflow-wrap:anywhere}
.stamp{flex-shrink:0;margin:6px 6px 0 0;border:4px double var(--ink);color:var(--ink);padding:8px 18px;border-radius:8px;font-size:26px;font-weight:800;letter-spacing:.06em;transform:rotate(-6deg);background:#fffdf7;max-width:240px;text-align:center;line-height:1.3}
.hero{display:grid;grid-template-columns:1.25fr 1fr 1fr;border-top:2px solid #1b1b18;border-bottom:1px solid #d9d2c3}
.stat{padding:16px 0 18px 22px;min-width:0}.stat:first-child{padding-left:0}
.stat+.stat{border-left:1px solid #d9d2c3}
.stat-label{display:block;font-size:17px;color:#5f594f;letter-spacing:.04em}
.stat-value{display:block;font-size:44px;line-height:1.15;font-weight:750;margin-top:6px;overflow-wrap:anywhere}
.stat:first-child .stat-value{font-size:64px;color:var(--ink)}
.stat .stat-value.small{font-size:30px;line-height:1.3;margin-top:12px}
.head+.meta{border-top:2px solid #1b1b18}
.meta{display:grid;grid-template-columns:1fr 1fr;column-gap:32px;margin:0 0 22px}
.fact{display:flex;flex-direction:column;gap:4px;min-width:0;border-bottom:1px solid #e2dbcd;padding:14px 0}
.fact.wide{grid-column:1/-1}
.fact-label{font-size:16px;letter-spacing:.06em;color:#5f594f}
.fact-value{font-size:24px;line-height:1.4;font-weight:650;overflow-wrap:anywhere}
.track{margin:0 0 22px;border:1px solid #d9d2c3;border-radius:12px;background:#fffdf7;overflow:hidden}
.track svg{display:block;width:100%;height:auto}
.track-caption{display:flex;justify-content:space-between;gap:16px;padding:10px 18px 12px;font-size:16px;color:#5f594f;border-top:1px solid #e2dbcd}
.t-grid{stroke:#e4ddcf;stroke-width:1}.t-tick{fill:#6b655b;font-size:15px;font-family:inherit}
.t-path{fill:none;stroke:var(--ink);stroke-width:3;stroke-dasharray:10 8;stroke-linecap:round}
.t-now{fill:var(--ink);stroke:#fffdf7;stroke-width:3}.t-future{fill:#fffdf7;stroke:var(--ink);stroke-width:3}
.t-label{fill:#1b1b18;font-size:18px;font-family:inherit;font-weight:650}.t-sub{fill:#5f594f;font-size:15px;font-family:inherit}
.voice{display:inline-block;background:var(--ink);color:#fffdf7;font-size:16px;letter-spacing:.06em;padding:4px 12px;border-radius:6px;margin-bottom:10px}
.lead{font-size:24px;line-height:1.75;margin:0 0 6px;color:#1b1b18;overflow-wrap:anywhere}
.more{font-size:16px;color:#5f594f;margin:0}
.foot{display:flex;justify-content:space-between;gap:16px;border-top:1px solid #d9d2c3;margin-top:22px;padding-top:12px;font-size:17px;color:#5f594f}
.foot span{min-width:0}.foot span:last-child{flex-shrink:0}
""".strip()

LEAD_LIMIT = 320


def _source(kind: str, item: Any) -> str:
    if kind == 'weather':
        return '中国气象局 / Jian Project'
    if kind == 'tsunami':
        return '自然资源部海啸预警中心 / Jian Project'
    if kind == 'typhoon':
        return '中国气象局 / EQSC'
    return f'{SOURCE_LABELS.get(item.source, item.source)} / Wolfx 公开接口'


def _kicker(kind: str, item: Any) -> str:
    if kind == 'weather':
        return '中国气象局 · 气象预警'
    if kind == 'tsunami':
        return '自然资源部海啸预警中心'
    if kind == 'typhoon':
        return '中国气象局 · 台风路径'
    # 发布机构跟随数据源：JMA 的地震不能写成「中国地震台网」。
    agency = '日本气象厅' if str(item.source).startswith('jma') else '中国地震台网'
    return agency + ' · ' + ('地震预警' if str(item.source).endswith('_eew') else '地震情报')


def _stamp(kind: str, item: Any, stopped: bool = False) -> str:
    if stopped:
        return '停编'
    if kind == 'weather':
        return f'{item.color}预警' if item.color != '未知' else '气象预警'
    if kind == 'tsunami':
        level = item.level or ''
        return f'{level}警报' if level in ('红色', '橙色', '黄色', '蓝色') else (level or '海啸')
    if kind == 'typhoon':
        return item.level or '台风'
    if str(item.source).endswith('_eew'):
        text = f'第 {item.report_num} 报'
        return text + (' · 最终报' if item.is_final else '')
    return '正式测定' if item.source == 'cenc' else '地震情报'


def _title(kind: str, item: Any, stopped: bool = False) -> str:
    if stopped:
        return '台风停编'
    if kind == 'weather':
        return item.title or item.headline or '气象预警'
    if kind == 'tsunami':
        return item.title or '海啸预警'
    if kind == 'typhoon':
        return f'{item.typhoon_id} {item.display_name}'
    magnitude = f'M{item.magnitude:.1f}' if item.magnitude is not None else '震级调查中'
    return f'{item.place or "震中调查中"} {magnitude}'



def _num(value: Any, unit: str, fmt: str = 'g') -> str:
    return '—' if value is None else f'{value:{fmt}} {unit}'


def _coord(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return '—'
    return f'{abs(lat):.1f}°{"N" if lat >= 0 else "S"} {abs(lon):.1f}°{"E" if lon >= 0 else "W"}'


def _short_time(value: Any) -> str:
    """'2025/09/25 23:00:00' → '9/25 23:00'；认不出的原样返回。"""
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})', str(value or ''))
    if not match:
        return str(value or '')
    _, month, day, hour, minute = match.groups()
    return f'{int(month)}/{int(day)} {int(hour):02d}:{minute}'


def _move(item: china.Typhoon) -> str:
    speed = str(item.move_speed or '').strip()
    if speed and re.fullmatch(r'\d+(?:\.\d+)?', speed):
        speed += ' km/h'
    return ' '.join(part for part in (item.move_direction, speed) if part) or '—'


def _intensity_label(event: EarthquakeEvent) -> str:
    return '最大震度' if str(event.source).startswith('jma') else '最大烈度'


def _hero(kind: str, item: Any) -> list[tuple[str, str]]:
    """三个关键数字：第一格最大、用强调色。"""
    if kind == 'typhoon':
        return [('最大风速', _num(item.wind_speed, 'm/s')), ('中心气压', _num(item.pressure, 'hPa')), ('移动', _move(item))]
    if kind == 'tsunami':
        return [('震级', f'M{item.magnitude:.1f}' if item.magnitude is not None else '—'),
                ('深度', _num(item.depth, 'km')), ('报次', f'第 {item.number} 报')]
    return [('震级', f'M{item.magnitude:.1f}' if item.magnitude is not None else '—'),
            ('深度', _num(item.depth, 'km')), (_intensity_label(item), item.intensity or '—')]


def _meta(kind: str, item: Any, config: Mapping[str, Any], stopped: bool = False) -> list[tuple[str, Any]]:
    """关键数字以外的字段；已在标题、印章或关键数字里出现的不再重复。"""
    if kind == 'weather':
        return [
            ('等级', f'{item.color}预警' if item.color != '未知' else '未知'),
            ('类型', china.weather_type_name(item)),
            ('地区', _weather_region(item) or '—'),
            ('生效', _time(item.effective_at) if item.effective_at else '—'),
        ]
    if kind == 'tsunami':
        return [
            ('震中', item.place or '—'),
            ('发震', _time(item.origin_time) if item.origin_time else '—'),
            ('发布', item.org_unit or '自然资源部海啸预警中心'),
            ('事件', item.alert_id or '—'),
        ]
    if kind == 'typhoon':
        rows = [
            ('编号', item.typhoon_id or '—'),
            ('中心位置', _coord(item.latitude, item.longitude)),
            ('更新', _time(item.updated_at) if item.updated_at else '—'),
        ]
        if not stopped:
            future = ' · '.join(part for part in (_time(item.future_time) if item.future_time else '',
                                                  item.future_level) if part)
            rows.append(('预报', future or '—'))
        return rows
    # 报次已在印章上（含「最终报」），不再重复；没配置本地坐标时不出「距本地」。
    distance = local_distance(item, config)
    rows = [
        ('来源', SOURCE_LABELS.get(item.source, item.source)),
        ('发震', _time(item.occurred_at) if item.occurred_at else '—'),
    ]
    if distance is not None:
        rows.append(('距本地', f'约 {distance:.0f} km'))
    return rows


def _step(span: float) -> float:
    for step in (0.5, 1, 2, 5, 10, 20):
        if span / step <= 7:
            return step
    return 30


def _track(item: china.Typhoon) -> str:
    """当前 → 预报位置，按经纬度等比例绘制（x 乘 cos 纬度），网格刻度是真实经纬度。
    缺任一组坐标就不画：只有一个点的网格没有信息量，当前位置已在字段里。"""
    if None in (item.latitude, item.longitude, item.future_latitude, item.future_longitude):
        return ''
    points = [(item.latitude, item.longitude), (item.future_latitude, item.future_longitude)]
    width, height, pad = 794, 300, 100
    k = math.cos(math.radians(sum(p[0] for p in points) / len(points)))
    xs = [p[1] * k for p in points]
    ys = [-p[0] for p in points]
    dx = max(max(xs) - min(xs), 3 * k)
    dy = max(max(ys) - min(ys), 3.0)
    scale = min((width - 2 * pad) / dx, (height - 2 * pad) / dy)
    top = 34  # 经度刻度放在顶部这条带里，不与底部的点标签相撞
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    def at(lat, lon):
        return width / 2 + (lon * k - cx) * scale, height / 2 + (-lat - cy) * scale

    lon_lo, lon_hi = (cx - width / 2 / scale) / k, (cx + width / 2 / scale) / k
    lat_lo, lat_hi = -(cy + height / 2 / scale), -(cy - height / 2 / scale)
    grid = []
    step = _step(lon_hi - lon_lo)
    lon = math.ceil(lon_lo / step) * step
    while lon <= lon_hi:
        x, _ = at(lat_lo, lon)
        grid.append(f'<path class="t-grid" d="M{x:.1f} {top}V{height}"/>')
        if 70 < x < width - 40:
            grid.append(f'<text class="t-tick" x="{x:.1f}" y="{top - 10}" text-anchor="middle">{abs(lon):g}°{"E" if lon >= 0 else "W"}</text>')
        lon += step
    step = _step(lat_hi - lat_lo)
    lat = math.ceil(lat_lo / step) * step
    while lat <= lat_hi:
        _, y = at(lat, lon_lo)
        if top < y < height:
            grid.append(f'<path class="t-grid" d="M0 {y:.1f}H{width}"/>')
            if y > top + 24:
                grid.append(f'<text class="t-tick" x="10" y="{y - 6:.1f}">{abs(lat):g}°{"N" if lat >= 0 else "S"}</text>')
        lat += step

    def label(x, y, title, sub, above=True):
        anchor = 'end' if x > width * 0.6 else 'start'
        tx = x - 20 if anchor == 'end' else x + 20
        ty = y - 18 if above else y + 34
        return (f'<text class="t-label" x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{_e(title)}</text>'
                + (f'<text class="t-sub" x="{tx:.1f}" y="{ty + 20:.1f}" text-anchor="{anchor}">{_e(sub)}</text>' if sub else ''))

    x0, y0 = at(*points[0])
    x1, y1 = at(*points[1])
    marks = [
        f'<path class="t-path" d="M{x0:.1f} {y0:.1f}L{x1:.1f} {y1:.1f}"/>',
        f'<circle class="t-future" cx="{x1:.1f}" cy="{y1:.1f}" r="9"/>',
        label(x1, y1, '预报', ' · '.join(p for p in (_short_time(item.future_time), item.future_level) if p), above=y1 <= y0),
        f'<circle class="t-now" cx="{x0:.1f}" cy="{y0:.1f}" r="10"/>',
        # 两个点上下错开标注，避免文字叠在一起。
        label(x0, y0, '当前', ' · '.join(p for p in (_short_time(item.updated_at), item.level) if p), above=y0 < y1),
    ]
    svg = (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="台风当前与预报位置">'
           f'<path class="t-grid" d="M0 {top}H{width}"/>' + ''.join(grid) + ''.join(marks) + '</svg>')
    km = distance_km(*points[0], *points[1])
    right = f'预报移动约 {km:,.0f} km' if km is not None else ''
    return (f'<section class="track">{svg}<div class="track-caption"><span>按经纬度比例绘制，未绘海岸线</span>'
            f'<span>{_e(right)}</span></div></section>')


def _lead(kind: str, item: Any, tone: str, stopped: bool = False) -> str:
    if stopped:
        if tone == 'character':
            return f'{bot_identity.display_name()}看了下：{item.typhoon_id} {item.display_name} 已停编。'
        return f'{item.typhoon_id} {item.display_name} 已停编。'
    if kind == 'weather':
        return china.weather_lead(item, tone)
    if kind == 'tsunami':
        return china.tsunami_lead(item, tone)
    if kind == 'typhoon':
        return china.typhoon_lead(item, tone)
    return event_lead(item, tone)


def _accent(kind: str, item: Any) -> str:
    if kind == 'weather':
        return _weather_accent(item.color)
    if kind == 'typhoon':
        return _typhoon_accent(item.level)
    if kind == 'tsunami':
        return _weather_accent(item.level)
    return _earthquake_accent(item)


def bulletin_html(kind: str, item: Any, config: Mapping[str, Any] | None = None,
                  tone: str = 'neutral', stopped: bool = False,
                  *, weather_map: weather_map_mod.WeatherMap | None = None) -> str:
    """四类 B Bulletin 卡片；所有外部文本转义。"""
    config = config or {}
    if kind not in ('weather', 'tsunami', 'typhoon', 'earthquake'):
        raise ValueError(f'未知灾害类型: {kind}')
    tone = tone if tone in ('neutral', 'character') else 'neutral'
    if kind == 'weather':
        return weather_card.weather_html(item, tone, weather_map)
    accent = _accent(kind, item)
    voice = f'<div class="voice">{_e(bot_identity.display_name())} · 后续解读</div>' if tone == 'character' else ''
    stats = _hero(kind, item)
    # 后两格任一放不下大字号时一起缩小，同一行的数字保持同一字号。
    small = ' small' if any(len(str(value)) > 6 for _, value in stats[1:]) else ''
    hero = '' if stopped else '<section class="hero">' + ''.join(
        f'<div class="stat"><span class="stat-label">{_e(label)}</span>'
        f'<b class="stat-value{small if i else ""}">{_e(value)}</b></div>'
        for i, (label, value) in enumerate(stats)) + '</section>'
    track = _track(item) if kind == 'typhoon' and not stopped else ''
    lead = ' '.join(_lead(kind, item, tone, stopped).split())
    more = ''
    if len(lead) > LEAD_LIMIT:
        lead, more = lead[:LEAD_LIMIT - 1] + '…', '<p class="more">完整内容见随附文字。</p>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>{BULLETIN_STYLE}</style></head>
<body><div class="card" data-card style="--accent:{accent};--ink:{_ink(accent)}">
  <div class="rail"></div>
  <main class="sheet">
    <div class="mast"><span>{_e(_kicker(kind, item))}</span><span>公开数据</span></div>
    <div class="head"><h1 class="title">{_e(_title(kind, item, stopped))}</h1><div class="stamp">{_e(_stamp(kind, item, stopped))}</div></div>
    {hero}
    <section class="meta">{_facts(_meta(kind, item, config, stopped))}</section>
    {track}
    {voice}
    <p class="lead">{_e(lead)}</p>{more}
    <footer class="foot"><span>{_e(_source(kind, item))}</span><span>仅供参考，以官方发布为准</span></footer>
  </main>
</div></body></html>"""


def card_html(event: EarthquakeEvent, config: Mapping[str, Any] | None = None,
              tone: str = 'neutral') -> str:
    return bulletin_html('earthquake', event, config, tone)


def _base_url(url: str) -> str:
    text = str(url or '').strip().rstrip('/')
    parsed = urlsplit(text)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise RenderError('渲染服务地址须以 http(s):// 开头')
    return text


async def render_png(base_url: str, html_text: str, *, width: int = 900,
                     height: int = 1200, timeout: float = 20.0,
                     token: str = '', client: httpx.AsyncClient | None = None,
                     whole_card: bool = False) -> bytes:
    import render_client as shared_render
    try:
        return await shared_render.render_html(html_text, url=_base_url(base_url),
            width=width, height=height, timeout_ms=int(timeout*1000),
            source='disaster', token=token, client=client, whole_card=whole_card)
    except shared_render.RenderError as exc:
        raise RenderError(str(exc)) from exc


async def health(base_url: str, *, timeout: float = 3.0,
                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    import render_client as shared_render
    url = shared_render.base_url() + '/health'
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
    try:
        response = await client.get(url)
        if response.status_code != 200:
            raise RenderError(f'渲染服务返回 HTTP {response.status_code}')
        data = response.json()
        if not isinstance(data, dict):
            raise RenderError('渲染服务返回格式无效')
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise RenderError(f'渲染服务不可达：{exc}') from exc
    finally:
        if own:
            await client.aclose()


async def render_bulletin(kind: str, item: Any, config: Mapping[str, Any],
                          tone: str = 'neutral', stopped: bool = False,
                          client: httpx.AsyncClient | None = None) -> bytes | None:
    """按面板配置渲染 B 卡；未启用/未配置地址返回 None，失败抛 RenderError。"""
    render = config.get('render') or {}
    if not render.get('enabled'):
        return None
    import render_client as shared_render
    remote_url = shared_render.base_url()
    timeout = float(render.get('timeout_seconds') or 20)
    weather_map = await weather_map_mod.map_for_alert(item, client=client) if kind == 'weather' else None
    # 气象卡保持原来的固定高度；地震/台风/海啸卡按内容截取整张 data-card。
    return await render_png(
        remote_url,
        bulletin_html(kind, item, config, tone, stopped, weather_map=weather_map),
        height=weather_card.card_height(item, tone) if kind == 'weather' else 1200,
        timeout=timeout,
        client=client,
        whole_card=kind != 'weather',
    )


async def render_event(event: EarthquakeEvent, config: Mapping[str, Any],
                       tone: str = 'neutral',
                       client: httpx.AsyncClient | None = None) -> bytes | None:
    return await render_bulletin('earthquake', event, config, tone, client=client)
