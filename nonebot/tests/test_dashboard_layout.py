"""Structural guardrails for the dashboard page layout.
    card = _between(text, '<section id="model-pref"', '</section>')
    assert 'id="modelBaseUrl"' in card
    assert 'id="modelApiKey"' in card
    assert 'id="modelProviderSave"' in card
    assert 'id="modelUpstreamFetch"' in card
    assert 'id="modelRestart"' in card
    assert 'fetchModelUpstream' in text and 'saveModelProvider' in text
    assert 'relay' not in card and 'echo-test' not in card

These tests are intentionally about the layout contract, not a specific card's
form. They catch the regressions that would quietly turn the panel back into
one long page, move cards into the overview, restore the 12-column grid, or
drop the mobile hooks.
"""
import re
from pathlib import Path

import dashboard


HTML = Path(dashboard.__file__).with_name('dashboard.html')


def _html():
    return HTML.read_text(encoding='utf-8')


def _between(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def _section_card_ids(fragment):
    ids = []
    for tag in re.findall(r'<section\b[^>]*>', fragment, re.S):
        if not re.search(r'\bclass="[^"]*\bcard\b', tag):
            continue
        match = re.search(r'\bid="([^"]+)"', tag)
        if match:
            ids.append(match.group(1))
    return ids


def _styles(text):
    return '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', text, re.S))


def test_overview_is_separate_from_all_column_cards():
    text = _html()
    overview = _between(text, '<div id="overview"', '<nav id="botBasicsNav"')
    assert 'class="overview-page"' in overview
    assert 'class="hero"' in overview
    assert 'class="kpis"' in overview
    assert _section_card_ids(overview) == []

    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    cards = _section_card_ids(panel)
    assert len(cards) >= 21
    assert len(cards) == len(set(cards))

    blocks = re.split(r'(?=<section\b)', panel)[1:]
    assert blocks
    assert all(re.search(r'<h2\b', block) for block in blocks)


def test_page_head_is_outside_the_pages_and_has_the_main_title():
    text = _html()
    head = _between(text, '<header class="page-head"', '</header>')
    assert re.search(r'<h1 id="pageTitle"', head)
    assert re.search(r'<p class="head-desc" id="pageDesc"', head)

    workspace = text.index('<main class="workspace">')
    overview = text.index('<div id="overview"')
    basics = text.index('<nav id="botBasicsNav"')
    panel = text.index('<div id="panelGrid"')
    footer = text.index('<footer class="page-footer"')
    assert workspace < text.index('<header class="page-head"') < overview < basics < panel < footer


def test_sidebar_links_map_to_real_cards_and_overview():
    text = _html()
    nav = re.search(r'<nav class="nav".*?</nav>', text, re.S).group(0)
    links = re.findall(r'<a [^>]*href="#([^"]+)"', nav)
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    basics_links = re.findall(r'<a [^>]*href="#([^"]+)"', basics)
    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    cards = set(_section_card_ids(panel))

    assert links[0] == 'overview'
    assert len(links) == len(set(links))
    assert len(basics_links) == len(set(basics_links))
    assert set(links) | set(basics_links) == cards | {'overview'}
    assert set(links) - {'overview'} <= cards
    assert set(basics_links) <= cards
    assert re.search(r'<nav id="botBasicsNav"[^>]*\bhidden\b', text)


def test_cards_stay_full_width_and_duplicate_card_titles_are_hidden():
    text = _html()
    styles = _styles(text)
    grid = re.search(r'#panelGrid\{([^}]*)\}', styles)
    assert grid is not None
    rule = grid.group(1)
    assert 'display:block' in rule
    assert 'grid-template-columns' not in rule
    assert 'grid-column' not in rule

    card_rule = re.search(r'#panelGrid > \.card\{([^}]*)\}', styles)
    assert card_rule is not None
    assert not re.search(r'(^|;)width:', card_rule.group(1))

    assert re.search(
        r'body\[data-page="section"\] #panelGrid > \.card:not\(\[hidden\]\) \.card-head h2\{display:none\}',
        styles,
    )

    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    for tag in re.findall(r'<section\b[^>]*>', panel, re.S):
        assert not re.search(r'grid-column|grid-template-columns|\bspan-\d', tag)


def test_model_settings_page_is_wired_to_the_basics_nav_and_loader():
    text = _html()
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    assert 'href="#model-pref"' in basics
    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    assert re.search(r'<section id="model-pref" class="card">', panel)
    assert re.search(r'<h2>模型设置</h2>', panel)
    assert "['#model-pref', loadModelPref]" in text
    assert 'async function loadModelPref' in text
    assert 'id="modelPrimary"' in panel and 'id="modelSecondary"' in panel


def test_mobile_layout_hooks_remain_present():
    text = _html()
    styles = _styles(text)
    assert '@media(max-width:800px)' in styles
    assert re.search(r'id="navToggle"[^>]*aria-controls="appSidebar"', text)
    assert 'id="navOverlay"' in text
    assert 'id="appSidebar"' in text
    assert 'id="appearanceButtonMobile"' in text
    assert re.search(r'\.topline-theme\{display:inline-flex\}', styles)


def test_holy_layout_has_no_right_context_rail():
    text = _html()
    styles = _styles(text)
    assert 'contextRail' not in text
    assert 'context-rail' not in text
    assert 'railLinks' not in text
    assert 'ON THIS PAGE' not in text
    assert 'margin-right:304px' not in styles
    assert not re.search(r'html\[data-skin="holy"\][^{}]*\.context-rail', styles)

    workspace = re.search(r'html\[data-skin="holy"\] \.workspace\{([^}]*)\}', styles)
    assert workspace is not None
    assert 'margin-right:0' in workspace.group(1)
    assert 'max-width:1440px' in workspace.group(1)


def test_shader_primary_action_uses_readable_theme_colors():
    styles = _styles(_html())
    rules = re.findall(
        r'html\[data-skin="shader"\] #app \.btn-primary(?::hover)?\{([^}]*)\}',
        styles,
    )
    assert rules, 'shader must override the generic .btn-primary ink/paper colors'
    joined = ' '.join(rules)
    assert 'background:var(--accent)' in joined
    assert 'color:var(--on-accent)' in joined
    assert 'color:var(--paper)' not in joined
