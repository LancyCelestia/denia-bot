# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
# 修改：D 浅色泡泡 HTML/CSS/SVG 模板，同机 Playwright 排版，Pillow 仅处理素材及输出缩放。
# 2026-11：资源卡等宽、温度均值移出环外、磁盘路径中间省略、趋势图加时间轴、日报每章一块面板、文字色满足 4.5:1。
from pathlib import Path
import math
import re
import random
import bot_identity
from card_document import document, text, image_uri, chunks, scale_pages
from render_client import RenderError, render_document, single_page

_BACKGROUND_FILE=Path(__file__).resolve().parent/'resources/statuscard/denia-bubbles.jpg'
# 集群卡与日报 / 周报的顶部横幅从这里随机取；状态卡主视觉固定用上面那张。
_BANNER_DIR=Path(__file__).resolve().parent/'resources/statuscard/banners'
_BANNER_SUFFIXES={'.jpg','.jpeg','.png','.webp'}
COLORS={'good':'#308977','warn':'#a77135','bad':'#bb4162'}
# 进度条/环用 COLORS；文字用同色系深一档，浅底上对比度 ≥ 4.5:1。
TEXT_COLORS={'good':'#2a7769','warn':'#8f5f1c','bad':'#bb4162'}
STYLE='''
.sheet{padding:50px;background:linear-gradient(#f4eef8,#f2ebf7);color:#352b49;font-size:18px}
.sheet>div,.sheet>section,.sheet>header{margin-bottom:20px}.panel{background:#fffcff;border-color:#e5d9ed;border-radius:20px;padding:20px}
.muted,footer{color:#6f5e82}.accent{color:#a65a89}h2{font-size:22px;font-weight:400}.hero{position:relative;height:483px}
.hero .art,.banner .art{position:absolute;left:-50px;top:-50px;width:1170px;height:840px;max-width:none;background-size:1430px auto;background-repeat:no-repeat;background-position:-140px 100px;mask-image:linear-gradient(to right,#0000000a 280px,#000 660px),linear-gradient(transparent 100px,#000 250px,#000 600px,transparent 820px);mask-composite:intersect;pointer-events:none}
.hero .identity,.banner>:not(.art){position:relative;z-index:1}.brand{display:flex;justify-content:space-between;font-size:18px;gap:12px}.brand>span{min-width:0}.brand>span:first-child{max-width:72%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.avatar{width:64px;height:64px;border-radius:50%;object-fit:cover;background:#f0e3f0;display:flex;align-items:center;justify-content:center;color:#a65a89;font-size:28px}.identity>.row{margin-top:54px;gap:18px}.nick{font-size:58px;margin:16px 0 0;max-width:440px}.subtitle{font-size:24px}.online{display:inline-block;border:1px solid;border-radius:24px;padding:8px 22px;background:#fffcff;font-size:20px;margin:24px 0 12px}.meta{font-size:19px;margin:8px 0;max-width:440px}
.resources{position:relative;display:flex;gap:16px}.resource{flex:1 1 0;min-width:0;text-align:center;padding:12px 18px 18px}.resource svg{width:148px;height:148px;display:block;margin:auto}.detail{font-size:17px;color:#6f5e82;line-height:1.4;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.resource h2{margin:4px 0}.disk{display:grid;grid-template-columns:300px 1fr 205px 75px;gap:15px;align-items:center;margin-top:12px}.track{height:16px;background:#ebe2f2;border-radius:10px;overflow:hidden}.fill{height:100%;border-radius:10px}.speeds{display:flex;gap:30px;font-size:34px;margin:6px 0 18px}.speeds>div{flex:1}.netmeta{font-size:17px;line-height:1.8}.columns{display:flex;gap:22px}.columns>section{flex:1;min-width:0}td,th{padding:6px 0;font-size:17px}.process td:first-child,.process th:first-child{width:58%}.process td:not(:first-child),.process th:not(:first-child){width:21%}.system-row{display:flex;gap:18px;justify-content:space-between;margin:15px 0;font-size:17px}.system-row span:first-child{color:#7e6c90;flex-shrink:0}.stats{display:flex;gap:12px}.stat{flex:1;padding:14px;text-align:center}.stat strong{display:block;font-size:25px;font-weight:400}.stat span{font-size:16px;color:#6f5e82}.foot{display:flex;justify-content:space-between;font-size:16px}.sites td{padding:8px 0}.sites td:first-child{width:55%}.sites td:last-child{text-align:right}.chart{width:100%;height:124px;display:block}.chart polyline{vector-effect:non-scaling-stroke}.axis{display:flex;justify-content:space-between;font-size:16px;color:#6f5e82;margin-top:6px}.report-title{font-size:44px}.report-row{display:flex;gap:28px;margin:10px 0}.report-row span:first-child{width:260px;color:#6f5e82;flex-shrink:0}.report-row span:last-child{overflow-wrap:anywhere}.report-table{table-layout:auto}.report-table td,.report-table th{padding:8px;font-size:17px;vertical-align:top}.report-table .n{white-space:nowrap;text-align:right}.report-table .k{white-space:nowrap}.report-table td.n{font-variant-numeric:tabular-nums}.report-block+.report-block{margin-top:18px}[data-render-continued]{font-size:18px;color:#6f5e82;padding:0 4px}
'''
# 日报 / 周报 / 集群卡共用同一张达妮娅背景：顶部出现，向下渐隐。裁切与蒙版跟着各自头部高度走。
BANNER_STYLE='''
.banner{position:relative;overflow:hidden}
.report-head,.cluster-head.banner{display:flex;flex-direction:column;justify-content:flex-end;min-height:340px}
.report-head{padding:0 4px 6px}
.report-head .art,.cluster-head.banner .art{height:460px;background-position:-140px -20px;mask-image:linear-gradient(to right,#0000000a 240px,#000 620px,#000 980px,transparent 1115px),linear-gradient(transparent 40px,#000 110px,#000 240px,transparent 330px)}
'''
# 集群卡专用：紧凑头部、摘要 chips、访客表列宽、存储行。沿用 STYLE 的浅色面板与资源环。
CLUSTER_STYLE='''
.cluster-head{padding:18px 22px}.cluster-head h1{font-size:34px;margin:6px 0 8px}
.cluster-status{font-size:18px;color:#6f5e82;margin:2px 0 10px}.cluster-status b{font-weight:400;color:#a65a89}
.chips{display:flex;flex-wrap:wrap;gap:10px}.chip{border:1px solid #e5d9ed;border-radius:16px;padding:6px 14px;font-size:16px;color:#6f5e82;background:#faf5fd}
.chip strong{font-weight:400;font-size:19px;color:#352b49;margin-left:6px}
.cluster-node .resources{margin-top:4px}.cluster-node .detail{margin-top:6px}
.cluster-guests td,.cluster-guests th{padding:6px 4px;font-size:16px}
.cluster-guests td:first-child,.cluster-guests th:first-child{width:27%}.cluster-guests td:nth-child(2),.cluster-guests th:nth-child(2){width:9%}
.cluster-guests td:nth-child(3),.cluster-guests th:nth-child(3){width:14%}.cluster-guests td:nth-child(4),.cluster-guests th:nth-child(4){width:12%}
.cluster-guests td:nth-child(5),.cluster-guests th:nth-child(5){width:25%}.cluster-guests td:nth-child(6),.cluster-guests th:nth-child(6){width:13%;text-align:right}
.cluster-storages .disk{grid-template-columns:175px 250px 1fr 195px 70px;margin-top:10px;font-size:16px}
.cluster-storages .track{height:14px}
'''
def number(value):
    try:
        n=float(value or 0)
        return n if math.isfinite(n) else 0
    except (TypeError,ValueError): return 0
def percent(value): return max(0,min(100,number(value)))
def shown(value): return text('--' if value is None else value)
def color(level, default='#308977'): return COLORS.get(str(level),default)
def ink(level, default='#2a7769'): return TEXT_COLORS.get(str(level),default)
def short_path(path, limit=30):
    """长挂载点保留开头一级和尽量多的结尾层级：/mnt/storage/media/library → /mnt/…/media/library。
    区分度在结尾，尾部截断会把几个盘都截成同一个前缀。"""
    path=str(path)
    if len(path)<=limit: return path
    parts=path.split('/')
    absolute=path.startswith('/')
    head='/'.join(parts[:2]) if absolute else parts[0]
    tail=[]
    for part in reversed(parts[2 if absolute else 1:]):
        if len(head)+len('/…/')+len('/'.join([part]+tail))>limit: break
        tail.insert(0,part)
    if not tail:
        # 最后一级本身就超长（如容器层哈希）：保留开头一级，再接最后一级的结尾。
        room=limit-len(head)-2
        return head+'/…'+parts[-1][-room:] if room>=8 else '…'+path[-(limit-1):]
    return head+'/…/'+'/'.join(tail)
def _clock(ts):
    from datetime import datetime, timedelta, timezone
    try: return datetime.fromtimestamp(float(ts),timezone(timedelta(hours=8))).strftime('%H:%M')
    except (TypeError,ValueError,OSError,OverflowError): return ''
def _span(start_ts, end_ts):
    try: span=max(0.0,float(end_ts)-float(start_ts))
    except (TypeError,ValueError): return ''
    return f'{span/3600:.0f} 小时' if span>=5400 else f'{span/60:.0f} 分钟'
def window(start_ts, end_ts):
    """「10:53–11:52（近 59 分钟）」；时间无效返回空串。"""
    left,right=_clock(start_ts),_clock(end_ts)
    return f'{left}–{right}（近 {_span(start_ts,end_ts)}）' if left and right else ''
def axis(start_ts, end_ts):
    """折线图下方的时间轴：起点、终点（北京时间）和跨度。"""
    left,right=_clock(start_ts),_clock(end_ts)
    if not left or not right: return ''
    return f'<div class="axis"><span>{left}</span><span>近 {_span(start_ts,end_ts)}</span><span>{right}</span></div>'
def trend(series, colors, ceiling=None):
    """折线图。ceiling 给定时纵轴固定 0..ceiling（百分比用 100），否则按峰值自动缩放。"""
    values=[[max(0,number(v)) for v in row] for row in series]
    peak=ceiling or max([v for row in values for v in row] or [1]) or 1
    lines=[]
    for row,stroke in zip(values,colors):
        if len(row)<2: continue
        points=' '.join(f'{i*1000/(len(row)-1):.2f},{100-v/peak*95:.2f}' for i,v in enumerate(row))
        lines.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="3"/>')
    return '<svg class="chart" viewBox="0 0 1000 124" preserveAspectRatio="none"><path d="M0 33H1000M0 66H1000M0 100H1000" stroke="#e5d9ed"/>'+''.join(lines)+'</svg>'

def human_speed(value):
    value=number(value)
    for unit in ('B/s','KB/s','MB/s','GB/s'):
        if value<1024 or unit=='GB/s':
            return f'{value:.1f} {unit}'
        value/=1024

def _art(background):
    return f'<div class="art" style="background-image:url(\'{background}\')"></div>' if background else ''

def hero_art():
    """状态卡主视觉：固定用户选定的 D 浅色泡泡。"""
    return _art(image_uri(_BACKGROUND_FILE,width=1600,height=900,quality=84))

def banner_files():
    """横幅候选：`resources/statuscard/banners/` 里的图片，外加状态卡那张作兜底。"""
    try:
        files=sorted(path for path in _BANNER_DIR.iterdir()
                     if path.is_file() and path.suffix.lower() in _BANNER_SUFFIXES)
    except OSError:
        files=[]
    if _BACKGROUND_FILE.is_file():
        files.append(_BACKGROUND_FILE)
    return files

def banner_art():
    """顶部横幅：随机取一张；文件损坏就换下一张，全都不行时退回纯色。"""
    files=banner_files()
    random.shuffle(files)
    for path in files:
        background=image_uri(path,width=1600,height=900,quality=84)
        if background:
            return _art(background)
    return ''

def build_status_html(view):
    bot=view.get('bot') or {}; network=view.get('network') or {}; compact=bool(view.get('compact'))
    name=bot_identity.display_name()
    avatar=image_uri(bot.get('avatar'),width=96,height=96)
    portrait=f'<img class="avatar" src="{avatar}">' if avatar else f'<div class="avatar">{text(name[:1] or "B")}</div>'
    online=bot.get('online'); online_text='在线' if online is True else '离线' if online is False else '状态未知'
    online_color=TEXT_COLORS['good'] if online is True else TEXT_COLORS['bad'] if online is False else '#6f5e82'
    blocks=[f'<header class="hero">{hero_art()}<div class="identity">'
        +f'<div class="brand"><span class="accent">{text(name)} / SERVER STATUS</span><span class="muted">{text(view.get("generated_at"))}</span></div>'
        +f'<div class="row">{portrait}<span class="accent">星穹观测</span></div><h1 class="nick ellipsis">{text(name)}</h1>'
        +f'<p class="subtitle muted">服务器状态</p><span class="online" style="color:{online_color}">● {online_text}</span>'
        +f'<p class="meta muted">已运行 {shown(view.get("uptime"))}</p>'
        +(f'<p class="meta muted ellipsis">QQ {text(bot["self_id"])}</p>' if bot.get('self_id') else '')
        +(f'<p class="meta muted ellipsis">{text(bot["protocol"])} {text(bot.get("protocol_version"))}</p>' if bot.get('protocol') else '')+'</div></header>']
    rings=[]
    for item in (view.get('resources') or [])[:3]:
        pct=percent(item.get('percent')); level=item.get('level') or ('bad' if pct>=90 else 'warn' if pct>=70 else 'good')
        accent={'cpu':'#308977','memory':'#a65a89','swap':'#9381c2'}.get(item.get('key'),'#308977')
        stroke=color(level,accent) if level in {'bad','warn'} else accent
        # 没有温度附加行的卡片（内存/SWAP）保持原来的居中位置；只有 CPU 上移给温度让位。
        # 环心只放百分比和当前温度；「60m 均 xx℃」原来 12px 挤在环里，移到环下的说明行开头。
        pct_y=76 if item.get('note') else 92
        rings.append(f'<section class="panel resource"><svg viewBox="0 0 160 160"><circle cx="80" cy="80" r="66" stroke="#ebe2f2" stroke-width="12" fill="none"/>'
            +f'<circle cx="80" cy="80" r="66" stroke="{stroke}" stroke-width="12" fill="none" pathLength="100" stroke-dasharray="{pct} 100" transform="rotate(-90 80 80)" stroke-linecap="round"/>'
            +f'<text x="80" y="{pct_y}" text-anchor="middle" fill="#352b49" font-size="32">{pct:.1f}%</text>'
            +(f'<text x="80" y="106" text-anchor="middle" fill="#352b49" font-size="20">{text(item.get("note"))}</text>' if item.get('note') else '')
            +f'</svg><h2>{text(item.get("title"))}</h2>'
            +f'<p class="detail">{text(" · ".join(part for part in (item.get("subnote"), item.get("detail")) if part))}</p></section>')
    if rings: blocks.append('<div class="resources">'+''.join(rings)+'</div>')
    for batch in chunks(view.get('disks') or [],12):
        rows=[]
        for disk in batch:
            c=color(disk.get('level'))
            rows.append(f'<div class="disk"><span class="ellipsis">{text(short_path(disk.get("path") or "--"))}</span><div class="track"><div class="fill" style="width:{percent(disk.get("percent"))}%;background:{c}"></div></div>'
                +f'<span class="muted ellipsis">{shown(disk.get("used"))} / {shown(disk.get("total"))}</span><span style="color:{ink(disk.get("level"))}">{shown(disk.get("percent_text"))}</span></div>')
        blocks.append('<section class="panel"><h2>磁盘</h2>'+''.join(rows)+'</section>')
    io=view.get('disk_io') or {}; history=network.get('chart') or []
    graph=trend([[row.get('up',0) for row in history],[row.get('down',0) for row in history]],['#308977','#a65a89']) if history else ''
    if history:
        peak=max(max(number(row.get('up')),number(row.get('down'))) for row in history)
        # 时间范围并进峰值这一行，不额外占高度：状态卡默认要放进一页。
        span=window(history[0].get('ts'),history[-1].get('ts'))
        graph=(f'<div class="row muted" style="justify-content:space-between;margin-top:12px"><span>峰值 {human_speed(peak)}{" · "+span if span else ""}</span><span><span style="color:{TEXT_COLORS["good"]}">↑ 上行</span>　<span class="accent">↓ 下行</span></span></div>'
               +graph)
    blocks.append('<section class="panel"><h2>网络</h2><div class="speeds">'
        +f'<div style="color:{TEXT_COLORS["good"]}">↑ {shown(network.get("up"))}</div><div class="accent">↓ {shown(network.get("down"))}</div></div>'
        +f'<p class="netmeta muted">本次开机累计 ↑ {shown(network.get("up_total"))} ↓ {shown(network.get("down_total"))}<br>'
        +f'{text(network.get("month") or "本月")}流量 ↑ {shown(network.get("month_up"))} ↓ {shown(network.get("month_down"))}<br>'
        +f'磁盘 IO 读 {shown(io.get("read"))} · 写 {shown(io.get("write"))} <span style="float:right">{text(network.get("interface"))}</span></p>'+graph+'</section>')
    for batch in chunks(view.get('websites') or [],15):
        rows=''.join(f'<tr><td class="ellipsis">{text(site.get("name") or "站点")}</td><td style="color:{TEXT_COLORS["good"] if site.get("ok") else TEXT_COLORS["bad"]}">{shown(site.get("status"))}</td><td>{shown(site.get("delay"))}</td></tr>' for site in batch)
        blocks.append('<section class="panel"><h2>网站延迟</h2><table class="sites">'+rows+'</table></section>')
    process=view.get('process') or {}; top=(process.get('top') or [])[:5 if compact else 10]
    rows=''.join(f'<tr><td class="ellipsis">{i+1}. {shown(item.get("name"))}</td><td>{shown(item.get("cpu"))}</td><td>{shown(item.get("mem"))}</td></tr>' for i,item in enumerate(top))
    proc='<section class="panel"><h2>Top 进程</h2>'+f'<p class="muted">共 {shown(process.get("total"))} 个 · 运行中 {shown(process.get("running"))}</p>'
    proc+='<table class="process"><tr><th>进程</th><th>CPU</th><th>内存</th></tr>'+rows+'</table>'+('' if top else '<p>暂无进程数据</p>')+'</section>'
    system=[] if compact else view.get('system') or []
    systems=['<section class="panel"><h2>系统信息</h2>'+''.join(f'<div class="system-row"><span>{text(row.get("label"))}</span><span class="ellipsis">{text(row.get("value"))}</span></div>' for row in batch)+'</section>' for batch in chunks(system,20)]
    blocks.append('<div class="columns">'+proc+(systems[0] if systems else '')+'</div>')
    blocks.extend(systems[1:])
    blocks.append('<div class="stats">'+''.join(f'<section class="panel stat"><strong>{shown(bot.get(key))}</strong><span>{label}</span></section>' for key,label in [('friends','好友'),('groups','群聊'),('members','群员'),('recv','收到'),('sent','回复')])+'</div>')
    blocks.append(f'<footer class="foot"><span>{text(view.get("footer") or "本机容器渲染 · 数据不出本机")}</span><span class="accent">{text(bot_identity.display_name())} · 浅色泡泡</span></footer>')
    return document(blocks,width=1170,css=STYLE,page_label=f'{name} · 服务器状态')

REPORT_PANEL_BUDGET=1800   # 单块面板的估算内容高度上限（px），留出页边距，保证一块放得进 2400 高的一页
_NUMERIC=re.compile(r'^(?:--|[-+]?[\d,.]+\s*(?:%|ms|s|[KMGT]?i?B(?:/s)?|次|条)?)$')


def _report_table(headers, rows):
    """数值列右对齐不换行，短列不换行，剩下的宽度留给最长的文本列（事件描述、站点名）。"""
    columns=max([len(headers)]+[len(row) for row in rows] or [0])
    kinds=[]
    for index in range(columns):
        cells=[str(row[index]) if index<len(row) else '' for row in rows]
        if cells and all(_NUMERIC.match(cell.strip()) for cell in cells):
            kinds.append('n')
        elif cells and max(len(cell) for cell in cells)<=14:
            kinds.append('k')
        else:
            kinds.append('')
    def cls(index):
        return f' class="{kinds[index]}"' if index<len(kinds) and kinds[index] else ''
    head='<tr>'+''.join(f'<th{cls(i)}>{text(v)}</th>' for i,v in enumerate(headers))+'</tr>'
    body=''.join('<tr>'+''.join(f'<td{cls(i)}>{text(cell)}</td>' for i,cell in enumerate(row))+'</tr>' for row in rows)
    return f'<table class="report-table">{head}{body}</table>'


def build_report_html(report):
    name=bot_identity.display_name()
    blocks=[f'<header class="banner report-head">{banner_art()}<p class="accent">{text(name)} · REPORT</p><h1 class="report-title">{text(report.get("title") or "日报")}</h1><p class="muted">{text(report.get("subtitle"))} {text(report.get("host"))}</p></header>']
    for section in report.get('sections') or []:
        # 每章默认一块面板；只有估算高度超出单页预算时才拆出「· 续」，不再把行、表、图各拆一块。
        pieces=[]
        for batch in chunks(section.get('rows') or [],18):
            pieces.append((42*len(batch),''.join(f'<div class="report-row"><span>{text(row[0])}</span><span>{text(row[1])}</span></div>' for row in batch)))
        chart=section.get('chart') or {}; points=chart.get('points') or []
        if len(points)>1:
            unit=str(chart.get('unit') or '')
            peak=max(number(row[1]) for row in points)
            scale=' · 纵轴 0–100%' if unit=='%' else ''
            pieces.append((200,trend([[row[1] for row in points]],['#a65a89'],ceiling=100 if unit=='%' else None)
                +axis(points[0][0],points[-1][0])
                +f'<p class="muted">{text(chart.get("label"))} · 峰值 {peak:.1f}{text(unit)}{scale}</p>'))
        table=section.get('table') or {}
        for batch in chunks(table.get('rows') or [],18):
            pieces.append((50+56*len(batch),_report_table(table.get('headers') or [],batch)))
        if section.get('note'): pieces.append((34,f'<p class="muted text">{text(section["note"])}</p>'))
        panels=[[]]; used=0
        for height,piece in pieces:
            if panels[-1] and used+height>REPORT_PANEL_BUDGET:
                panels.append([]); used=0
            panels[-1].append(piece); used+=height
        for i,panel in enumerate(panels):
            body=''.join(f'<div class="report-block">{piece}</div>' for piece in panel)
            blocks.append(f'<section class="panel"><h2 class="accent">{text(section.get("title"))}{" · 续" if i else ""}</h2>{body}</section>')
    blocks.append(f'<footer>{text(report.get("footer"))}</footer>')
    return document(blocks,width=1170,css=STYLE+BANNER_STYLE,page_label=f'{name} · {report.get("title") or "日报"}')

def render_status_card_pages(view, *, width=1170):
    return scale_pages(render_document(build_status_html(view),width=1170,source='statuscard'),max(760,min(2880,int(width))))
def render_report_pages(report, *, width=1170):
    return scale_pages(render_document(build_report_html(report),width=1170,source='status_report'),max(760,min(2880,int(width))))
def render_status_card(view, *, width=1170):
    return single_page(render_status_card_pages(view,width=width))
def render_report(report, *, width=1170):
    return single_page(render_report_pages(report,width=width))
def _cluster_status(summary):
    nodes=int(number(summary.get('nodes')))
    if not nodes: return '未获取到节点数据'
    return f'节点 {nodes} · 访客 {int(number(summary.get("running")))}/{int(number(summary.get("guests")))} 运行 · 存储 {int(number(summary.get("storages")))}'
def _cluster_chip(label, value):
    return f'<span class="chip">{text(label)}<strong>{shown(value)}</strong></span>'
def _cluster_ring(title, value, level='', accent='#308977', note='', detail=''):
    has_value=value is not None
    pct=percent(value) if has_value else 0
    stroke=color(level,accent) if str(level) in {'bad','warn'} else accent
    # 环心固定显示百分比（无数据为 --），长文本放到环下方，避免撑破 SVG 被裁切。
    center=f'{pct:.1f}%' if has_value else '--'
    pct_y=74 if note else 90
    return (f'<section class="panel resource"><svg viewBox="0 0 160 160"><circle cx="80" cy="80" r="66" stroke="#ebe2f2" stroke-width="12" fill="none"/>'
        +f'<circle cx="80" cy="80" r="66" stroke="{stroke}" stroke-width="12" fill="none" pathLength="100" stroke-dasharray="{pct} 100" transform="rotate(-90 80 80)" stroke-linecap="round"/>'
        +f'<text x="80" y="{pct_y}" text-anchor="middle" fill="#352b49" font-size="32">{center}</text>'
        +(f'<text x="80" y="100" text-anchor="middle" fill="#352b49" font-size="19">{text(note)}</text>' if note else '')
        +f'</svg><h2>{text(title)}</h2>'
        +(f'<p class="detail">{text(detail)}</p>' if detail else '')
        +'</section>')
def _cluster_node(node):
    node=node or {}
    # cpu_note 与 temperature_text 在真实数据里常是同一温度，只显示一行。
    rings=[_cluster_ring('CPU',node.get('cpu_percent'),node.get('cpu_level'),'#308977',node.get('cpu_note') or node.get('temperature_text')),
        _cluster_ring('内存',node.get('memory_percent'),node.get('memory_level'),'#a65a89',detail=node.get('memory_text')),
        _cluster_ring('SWAP',node.get('swap_percent'),node.get('swap_level'),'#9381c2',detail=node.get('swap_text'))]
    detail=[]
    if node.get('cores') not in (None,''): detail.append(f'{shown(node.get("cores"))} 核')
    if node.get('load') not in (None,''): detail.append('负载 '+text(node.get('load')))
    if node.get('uptime') not in (None,''): detail.append('运行 '+text(node.get('uptime')))
    status=f'<span style="color:{ink(node.get("status_level"))}">● {text(node.get("status"))}</span>' if node.get('status') not in (None,'') else ''
    detail_html='<p class="detail">'+' · '.join(detail)+('　'+status if status else '')+'</p>' if detail or status else ''
    return f'<section class="panel cluster-node"><h2>{text(node.get("name") or "未命名节点")}</h2><div class="resources">'+''.join(rings)+'</div>'+detail_html+'</section>'
def _cluster_guest_row(guest):
    guest=guest or {}; vmid=guest.get('vmid')
    identity=text(guest.get('name') or '未命名')+(f'<span class="muted"> #{text(vmid)}</span>' if vmid not in (None,'') else '')
    kind=guest.get('type_label') or guest.get('type'); status=guest.get('status_label') or guest.get('status')
    status_html=f'<span style="color:{ink(guest.get("status_level"))}">{text(status)}</span>' if status not in (None,'') else '<span class="muted">--</span>'
    return ('<tr><td class="ellipsis">'+identity+'</td>'+f'<td>{shown(kind)}</td><td>{status_html}</td>'
        +f'<td>{shown(guest.get("cpu_text"))}</td><td class="ellipsis">{shown(guest.get("mem_text"))}</td><td>{shown(guest.get("uptime"))}</td></tr>')
def _cluster_guest_sections(guests):
    if not guests: return ['<section class="panel"><h2>访客</h2><p class="muted">没有访客数据</p></section>']
    groups={}
    for guest in guests: groups.setdefault((guest or {}).get('node') or '未指定节点',[]).append(guest)
    sections=[]
    for node,items in groups.items():
        for index,batch in enumerate(chunks(items,12)):
            rows=''.join(_cluster_guest_row(guest) for guest in batch)
            sections.append('<section class="panel"><h2>访客 · '+text(node)+(' · 续' if index else '')+'</h2><table class="cluster-guests">'
                +'<tr><th>访客</th><th>类型</th><th>状态</th><th>CPU</th><th>内存</th><th>运行</th></tr>'+rows+'</table></section>')
    return sections
def _cluster_storage_row(storage):
    storage=storage or {}; c=color(storage.get('level'))
    pct=percent(storage.get('percent')) if storage.get('percent') is not None else 0
    meta=' · '.join(text(part) for part in (storage.get('type'),storage.get('content')) if part not in (None,''))
    if storage.get('active') is False: meta=meta+' · 未激活' if meta else '未激活'
    return ('<div class="disk cluster-storage"><span class="ellipsis">'+shown(storage.get('name') or '未命名存储')+'</span>'
        +f'<span class="muted ellipsis">{meta}</span><div class="track"><div class="fill" style="width:{pct}%;background:{c}"></div></div>'
        +f'<span class="muted ellipsis">{shown(storage.get("used_text"))} / {shown(storage.get("total_text"))}</span>'
        +f'<span style="color:{ink(storage.get("level"))}">{shown(storage.get("percent_text"))}</span></div>')
def _cluster_storage_sections(storages):
    if not storages: return ['<section class="panel cluster-storages"><h2>存储</h2><p class="muted">没有存储数据</p></section>']
    return ['<section class="panel cluster-storages"><h2>存储'+(' · 续' if index else '')+'</h2>'+''.join(_cluster_storage_row(storage) for storage in batch)+'</section>'
            for index,batch in enumerate(chunks(storages,12))]
def build_cluster_html(view):
    view=view or {}; summary=view.get('summary') or {}
    chips=''.join(_cluster_chip(label,summary.get(key)) for key,label in [('nodes','节点'),('guests','访客'),('running','运行中'),('vms','VM'),('lxcs','LXC'),('storages','存储')])
    blocks=[f'<header class="panel cluster-head banner">{banner_art()}<div class="brand"><span class="accent">{text(bot_identity.display_name())} / PROXMOX VE</span><span class="muted">{shown(view.get("generated_at"))}</span></div>'
        +f'<h1 class="ellipsis">{text(view.get("title") or "Proxmox VE")}</h1>'
        +f'<p class="cluster-status">{text(_cluster_status(summary))}</p><div class="chips">{chips}</div></header>']
    nodes=view.get('nodes') or []
    if nodes: blocks.extend(_cluster_node(node) for node in nodes)
    else: blocks.append('<section class="panel"><h2>节点</h2><p class="muted">没有节点数据</p></section>')
    blocks.extend(_cluster_guest_sections(view.get('guests') or []))
    blocks.extend(_cluster_storage_sections(view.get('storages') or []))
    blocks.append(f'<footer class="foot"><span>数据 {shown(view.get("source"))} · 本地渲染</span></footer>')
    return document(blocks,width=1170,css=STYLE+BANNER_STYLE+CLUSTER_STYLE,page_label=f'{bot_identity.display_name()} · {view.get("title") or "Proxmox VE"}')
def render_cluster_card_pages(view, *, width=1170):
    return scale_pages(render_document(build_cluster_html(view),width=1170,source='cluster'),max(760,min(2880,int(width))))
def render_cluster_card(view, *, width=1170):
    return single_page(render_cluster_card_pages(view,width=width))
