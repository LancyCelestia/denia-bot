"""Group topics ranking card (MIT). No longer reuses the weather template; it is now a ranked bar list.

Input is `group_topics_logic.top()` as-is: [(word, count), ...], already sorted by count descending.
"""
from card_document import document, text
from card_theme import LIGHT_CSS, THEME, brand, footer, header
from render_client import render_document, single_page

WIDTH = 760

CSS = '''
.rank{padding:14px 28px}
.r{display:grid;grid-template-columns:44px 170px minmax(0,1fr) 104px;align-items:center;gap:16px;padding:9px 0}
.n{font-size:20px;color:var(--muted)}
.w{font-size:26px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:14px;border-radius:7px;background:var(--soft);overflow:hidden}
.bar i{display:block;height:100%;min-width:6px;border-radius:7px;background:#cdb9da}
.r.top .bar i{background:var(--accent)}.r.top .w{color:var(--accent)}
.c{text-align:right;font-size:22px;white-space:nowrap}.c small{font-size:17px;color:var(--muted);margin-left:2px}
.light .title{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
'''


def headline(rows):
    word, count = rows[0]
    return f'「{word}」聊得最多，{count} 条消息提到'


def build_html(group_id, rows, *, window='最近约 7 天'):
    rows = [(str(word), int(count)) for word, count in rows]
    if not rows:
        raise ValueError('没有话题数据')
    peak = max(count for _, count in rows) or 1
    body = ''.join(
        f'<div class="r{" top" if i < 3 else ""}"><span class="n">{i + 1:02d}</span>'
        f'<span class="w">{text(word)}</span>'
        f'<div class="bar"><i style="width:{count / peak * 100:.1f}%"></i></div>'
        f'<span class="c">{count}<small>条</small></span></div>'
        for i, (word, count) in enumerate(rows))
    blocks = [header(brand('群话题'), f'群 {group_id} · {window}', headline(rows)),
              '<section class="panel rank">' + body + '</section>',
              footer('按提到该词的消息数排序', '不保存消息原文')]
    return document(blocks, width=WIDTH, css=LIGHT_CSS + CSS, theme=THEME, page_label=brand('群话题'))


def render_pages(group_id, rows):
    return render_document(build_html(group_id, rows), width=WIDTH, source='group_topics')


def render(group_id, rows):
    return single_page(render_pages(group_id, rows))
