"""Weather cards rendered by the shared Playwright container.

- `render_pages(view)`: the new layout. Pass the dict returned by `weather_service.fetch_view` and it is
  laid out as「实况」or「预报」depending on `view['kind']`.
- `render_pages(text, heading, subtitle)`: the old text input keeps working, so existing callers and
  tests don't break. It now also uses the light theme, one panel per line.
"""
from card_document import document, text, paragraphs
from card_theme import LIGHT_CSS, THEME, brand, footer, header
from render_client import render_document, single_page

WIDTH = 760

NOW_CSS = '''
.now{display:flex;align-items:flex-end;gap:36px;padding:28px 36px 32px}
.temp{font-size:156px;line-height:.9;letter-spacing:-4px;white-space:nowrap}
.temp .deg{color:var(--accent);margin-left:4px}
/* 数字靠底线对齐天气文字；「--」画在行高中间，底对齐会显得偏上，缺温度时改为居中对齐。 */
.now.missing{align-items:center}.now.missing .cond{padding-bottom:0}
.temp.missing{color:var(--muted);font-size:120px;letter-spacing:0}
.cond{padding-bottom:10px;min-width:0}.cond-text{font-size:44px;line-height:1.2;overflow-wrap:anywhere}
.feel{font-size:22px;color:var(--muted);margin-top:8px}
.facts{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;padding:0}
.fact{padding:18px 24px;min-width:0}.fact+.fact{border-left:1px solid var(--line)}
.fact span{display:block;font-size:18px;color:var(--muted)}
.fact b{display:block;font-size:26px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
'''

FORECAST_CSS = '''
.days{padding:8px 28px}
.day{display:grid;grid-template-columns:100px minmax(0,1fr) 60px 180px 60px;align-items:center;gap:14px;padding:18px 0}
.day+.day{border-top:1px solid var(--line)}
.when b{display:block;font-size:26px;line-height:1.2}.when span{font-size:18px;color:var(--muted)}
.wx{font-size:24px;line-height:1.3;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.lo{text-align:right;color:var(--muted);font-size:24px}.hi{font-size:24px}
.range{position:relative;height:12px;border-radius:6px;background:var(--soft)}
.range i{position:absolute;top:0;bottom:0;min-width:12px;border-radius:6px;background:var(--teal)}
.day.today .range i{background:var(--accent)}
'''

TEXT_CSS = '.light .text{white-space:pre-wrap;overflow-wrap:anywhere}'


def _when(view):
    return f'{view["queried_at"]} 查询' if view.get('queried_at') else '和风天气'


def _deg(value):
    return '—' if value is None else f'{value}°'


def feels_text(temperature, feels_like):
    if feels_like is None:
        return ''
    if temperature is None:
        return f'体感 {feels_like}°'
    diff = round(feels_like - temperature, 1)
    diff = int(diff) if float(diff).is_integer() else diff
    if diff == 0:
        return f'体感 {feels_like}°，和实际一致'
    return f'体感 {feels_like}°，比实际{"低" if diff < 0 else "高"} {abs(diff)}°'


def build_now_html(view):
    temp = view.get('temperature')
    blocks = [header(brand('天气实况'), _when(view), view.get('place') or view.get('city') or '天气'),
              f'<section class="panel now{"" if temp is not None else " missing"}">'
              + (f'<div class="temp">{text(temp)}<span class="deg">°</span></div>' if temp is not None else '<div class="temp missing">--</div>')
              + f'<div class="cond"><p class="cond-text">{text(view.get("condition") or "未知")}</p>'
              + (f'<p class="feel">{text(feels_text(temp, view.get("feels_like")))}</p>' if view.get('feels_like') is not None else '')
              + '</div></section>']
    facts = view.get('facts') or []
    if facts:
        blocks.append('<section class="panel facts">' + ''.join(
            f'<div class="fact"><span>{text(k)}</span><b>{text(v)}</b></div>' for k, v in facts[:3]) + '</section>')
    city = str(view.get('city') or '')[:10]
    blocks.append(footer('数据来源：和风天气', f'发「.天气预报 {city}」看预报' if city else ''))
    return document(blocks, width=WIDTH, css=LIGHT_CSS + NOW_CSS, theme=THEME, page_label=brand('天气实况'))


def forecast_lead(days):
    """标题下的一句结论：哪天最冷、比今天低几度；数据不全时退回区间描述。"""
    usable = [d for d in days if d.get('min') is not None and d.get('max') is not None]
    if not usable:
        return ''
    lows = [d['min'] for d in usable]
    coldest = usable[lows.index(min(lows))]
    first = usable[0]
    drop = first['min'] - coldest['min']
    if coldest is first or drop <= 0:
        return f'{first["label"]}最冷，{len(usable)} 天气温 {min(lows)}°～{max(d["max"] for d in usable)}°' if len(usable) > 1 else ''
    drop = int(drop) if float(drop).is_integer() else round(drop, 1)
    return f'{coldest["label"]}最低 {coldest["min"]}°，比{first["label"]}低 {drop}°'


def build_forecast_html(view):
    days = view.get('days') or []
    usable = [d for d in days if d.get('min') is not None and d.get('max') is not None]
    lo = min((d['min'] for d in usable), default=0)
    hi = max((d['max'] for d in usable), default=0)
    span = (hi - lo) or 1
    rows = []
    for i, d in enumerate(days):
        if d.get('min') is not None and d.get('max') is not None:
            left = (d['min'] - lo) / span * 100
            width = max(0, d['max'] - d['min']) / span * 100
            bar = f'<div class="range"><i style="left:{left:.1f}%;width:{width:.1f}%"></i></div>'
        else:
            bar = '<div class="range"></div>'
        rows.append(f'<div class="day{" today" if i == 0 else ""}"><div class="when"><b>{text(d.get("label"))}</b>'
                    f'<span>{text(d.get("short"))}</span></div><div class="wx">{text(d.get("condition"))}</div>'
                    f'<div class="lo">{_deg(d.get("min"))}</div>{bar}<div class="hi">{_deg(d.get("max"))}</div></div>')
    note = f'温度条按 {len(usable)} 天共用刻度 {lo}°～{hi}°' if len(usable) > 1 else ''
    blocks = [header(brand('天气预报'), _when(view), view.get('place') or view.get('city') or '天气预报',
                     forecast_lead(days)),
              '<section class="panel days">' + ''.join(rows) + '</section>',
              footer('数据来源：和风天气', note)]
    return document(blocks, width=WIDTH, css=LIGHT_CSS + FORECAST_CSS, theme=THEME, page_label=brand('天气预报'))


def build_text_html(value, heading='天气', subtitle='和风天气 · 天气信息'):
    """旧接口：多行文字 → 一行一块。第一行是地点，「数据来源：」行放页脚。"""
    lines = [line.strip() for line in str(value).splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError('天气内容为空')
    sources = [line for line in lines[1:] if line.startswith('数据来源：')]
    entries = [line for line in lines[1:] if not line.startswith('数据来源：')]
    if not entries:
        raise ValueError('天气内容为空')
    blocks = [header(brand(heading), subtitle, lines[0])]
    for line in entries:
        blocks.extend(paragraphs(line, chars=500))
    blocks.extend(paragraphs('\n'.join(sources), class_name='muted text'))
    return document(blocks, width=WIDTH, css=LIGHT_CSS + TEXT_CSS, theme=THEME, page_label=brand(heading))


def build_html(value, heading='天气', subtitle='和风天气 · 天气信息'):
    if isinstance(value, dict):
        return build_forecast_html(value) if value.get('kind') == 'forecast' else build_now_html(value)
    return build_text_html(value, heading, subtitle)


def render_pages(value, heading='天气', subtitle='和风天气 · 天气信息', *, source='weather'):
    return render_document(build_html(value, heading, subtitle), width=WIDTH, source=source)


def render(value, heading='天气', subtitle='和风天气 · 天气信息'):
    return single_page(render_pages(value, heading, subtitle))
