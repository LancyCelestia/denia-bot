# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。改为共享同机 Playwright 渲染；2026-11 改版：封面、倒计时、浅色主题。
"""Epic 喜加一卡片。领取链接不进图片，由插件作为下一条文字消息发送（logic.format_links）。

covers 是 epic_free_service.fetch_covers 的结果 {cover_url: bytes}；缺图时该款只是不显示封面。
"""
from card_document import document, text, image_uri
from card_theme import LIGHT_CSS, THEME, brand, footer, header
from render_client import render_document, single_page
import epic_free_logic as logic

WIDTH = 760
# 封面内嵌进 HTML，请求体上限 2 MiB：大图最多 4 张，其余只显示文字。
MAX_LARGE_COVERS = 4

CSS = '''
.game{padding:0;overflow:hidden}
.cover{display:block;width:100%;max-width:none;aspect-ratio:16/9;object-fit:cover;background:var(--soft)}
.info{padding:20px 28px 24px}
.g-title{font-size:30px;line-height:1.35;margin:0;overflow-wrap:anywhere}
.desc{font-size:20px;color:var(--muted);margin-top:8px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.deal{display:flex;align-items:center;gap:14px;margin-top:16px;flex-wrap:wrap}
.was{font-size:20px;color:var(--muted);text-decoration:line-through}
.free{font-size:22px;color:#fff;background:var(--teal);border-radius:8px;padding:2px 12px}
.until{margin-left:auto;font-size:20px}
.soon{padding:20px 28px}
.soon h2{font-size:22px;color:var(--muted);margin:0 0 6px}
.s{display:grid;grid-template-columns:160px minmax(0,1fr);gap:18px;align-items:center;padding:12px 0}
.s.no-thumb{grid-template-columns:minmax(0,1fr)}
.s+.s{border-top:1px solid var(--line)}
.thumb{display:block;width:160px;max-width:none;aspect-ratio:16/9;object-fit:cover;border-radius:10px;background:var(--soft)}
.s-title{font-size:24px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s-time{font-size:18px;color:var(--muted)}
'''


def _cover(game, covers, *, width, height, cls):
    raw = (covers or {}).get(game.get('cover_url') or '')
    uri = image_uri(raw, width=width, height=height, quality=82) if raw else ''
    return f'<img class="{cls}" src="{uri}" alt="">' if uri else ''


def _title(free, now):
    if not free:
        return '本周没有免费游戏'
    remaining = logic.remaining_text(free, now)
    return f'本周 {len(free)} 款免费' + (f'，{remaining}' if remaining else '')


def build_html(free, upcoming, width=WIDTH, *, covers=None, now=None):
    free, upcoming = list(free or ()), list(upcoming or ())[:3]
    blocks = [header(brand('Epic 喜加一'), '北京时间', _title(free, now))]
    for index, game in enumerate(free):
        cover = _cover(game, covers, width=720, height=405, cls='cover') if index < MAX_LARGE_COVERS else ''
        original = str(game.get('original_price') or '').strip()
        description = logic.truncate(game.get('description'), 120)
        blocks.append('<section class="panel game">' + cover + '<div class="info">'
                      f'<h2 class="g-title">{text(game.get("title"))}</h2>'
                      + (f'<p class="desc">{text(description)}</p>' if description else '')
                      + '<div class="deal">'
                      + (f'<span class="was">{text(original)}</span>' if original else '')
                      + '<span class="free">免费</span>'
                      + f'<span class="until">截止 {text(logic.format_short(game["end"]))}</span>'
                      '</div></div></section>')
    if upcoming:
        rows = []
        for game in upcoming:
            thumb = _cover(game, covers, width=320, height=180, cls='thumb')
            rows.append(f'<div class="s{"" if thumb else " no-thumb"}">{thumb}<div style="min-width:0">'
                        f'<p class="s-title">{text(game.get("title"))}</p>'
                        f'<p class="s-time">{text(logic.format_short(game["start"]))} 开始</p></div></div>')
        blocks.append('<section class="panel soon"><h2>下一期</h2>' + ''.join(rows) + '</section>')
    blocks.append(footer('领取链接见下一条文字消息' if free or upcoming else '数据来源 Epic Games Store', '换期才推送'))
    return document(blocks, width=width, css=LIGHT_CSS + CSS, theme=THEME, page_label=brand('Epic 喜加一'))


def render_pages(free, upcoming, width=WIDTH, *, covers=None, now=None):
    return render_document(build_html(free, upcoming, width, covers=covers, now=now), width=width, source='epic')


def render(free, upcoming, width=WIDTH, *, covers=None, now=None):
    return single_page(render_pages(free, upcoming, width, covers=covers, now=now))
