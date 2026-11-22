"""Shared light card theme for the simple cards (weather / group topics / Epic). MIT.

Same palette as the D「浅色泡泡」 status card, so all of the bot's cards look like one set.
Rules:
- Every text colour has at least 4.5:1 contrast on the panel and background.
- Only weight 400 is used: HYSongYunLangHeiW has no bold, and Chromium would synthesise fake bold.
- Nothing here draws a layout. Each business module keeps its own blocks; this module only
  provides the colour variables, the shared header and the footer.
"""
from __future__ import annotations

from card_document import text

THEME = 'light'

# Scoped under `.sheet.light` so it overrides card_document.BASE_CSS without affecting
# the status card, parser card or other templates.
LIGHT_CSS = '''
.sheet.light{--ink:#352b49;--muted:#6a5a7d;--accent:#9a5280;--teal:#2a7769;--bg:#f4eef8;--panel:#fffcff;--line:#e5d9ed;--soft:#ebe2f2;
  padding:40px;background:var(--bg);color:var(--ink);font-size:22px;line-height:1.5;font-variant-numeric:tabular-nums}
.sheet.light,.sheet.light *{font-weight:400}
.sheet.light>*{margin:0 0 20px}.sheet.light>:last-child{margin-bottom:0}
/* :where() 把特异性降到和单个类相同，各卡片自己的 .game/.now 等 padding 才能覆盖默认值 */
:where(.light) .panel{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:24px 28px}
.light .kicker{display:flex;justify-content:space-between;gap:16px;font-size:18px;color:var(--muted)}
.light .kicker>span{min-width:0}.light .kicker .brand{color:var(--accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.light .kicker>span:last-child{flex-shrink:0}
.light .title{font-size:40px;line-height:1.3;margin:12px 0 0;overflow-wrap:anywhere}
.light .lead{font-size:22px;color:var(--muted);margin-top:6px}
.light .muted{color:var(--muted)}
.light footer{display:flex;justify-content:space-between;gap:16px;font-size:18px;color:var(--muted);padding:0 4px}
.light footer>span{min-width:0}
.light [data-render-continued]{font-size:18px;color:var(--muted);padding:0 4px}
'''


def header(brand: str, right: str, title: str, lead: str = '') -> str:
    return ('<header><div class="kicker">'
            f'<span class="brand">{text(brand)}</span><span>{text(right)}</span></div>'
            f'<h1 class="title">{text(title)}</h1>'
            + (f'<p class="lead">{text(lead)}</p>' if lead else '') + '</header>')


def footer(left: str, right: str = '') -> str:
    return f'<footer><span>{text(left)}</span><span>{text(right)}</span></footer>'


def brand(suffix: str) -> str:
    """「达妮娅 · 天气实况」：名字跟随 bot_identity，取不到时退回通用名。"""
    try:
        import bot_identity
        name = bot_identity.display_name()
    except Exception:
        name = ''
    return f'{name} · {suffix}' if name else suffix
