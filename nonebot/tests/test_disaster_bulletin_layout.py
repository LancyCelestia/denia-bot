"""Layout contract for the redesigned disaster B Bulletin cards: pure HTML, no renderer needed.

Real screenshots go through the renderer; this file only locks the structural decisions
(stamp in normal flow, no fake figures, readable font sizes, whole-card capture).
"""
import asyncio
import dataclasses
import json
import re
import sys
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from PIL import Image

import disaster_china_logic as china
import disaster_logic as logic
import disaster_render as render
from tests.test_disaster_china_logic import EQSC_TYPHOON, JIAN_TSUNAMI, JIAN_WEATHER

LOCAL = {'local': {'enabled': True, 'latitude': 30.25, 'longitude': 120.13}}


def png(size=(900, 700)):
    out = BytesIO()
    Image.new('RGB', size, 'white').save(out, 'PNG')
    return out.getvalue()


def cards():
    event = logic.sample_event()
    typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
    tsunami = china.parse_jian_tsunami(JIAN_TSUNAMI)
    return {
        'earthquake': render.bulletin_html('earthquake', event, LOCAL),
        'earthquake-character': render.bulletin_html('earthquake', event, {}, 'character'),
        'typhoon': render.bulletin_html('typhoon', typhoon, {}),
        'typhoon-stopped': render.bulletin_html('typhoon', typhoon, {}, 'character', stopped=True),
        'tsunami': render.bulletin_html('tsunami', tsunami, {}),
    }


class BulletinLayoutTest(unittest.TestCase):
    def test_every_card_is_one_self_contained_data_card(self):
        for name, html in cards().items():
            with self.subTest(name):
                self.assertEqual(html.count('data-card'), 1)
                self.assertNotIn('<script', html)
                self.assertNotRegex(html, r'(?:src|href)="https?://')

    def test_no_placeholder_figures_or_dev_notes(self):
        for name, html in cards().items():
            with self.subTest(name):
                for phrase in ('生产接', '非地理精确', '官方图件占位', '路径/风圈为示意', 'class="viz'):
                    self.assertNotIn(phrase, html)

    def test_stamp_sits_in_the_title_row(self):
        html = cards()['earthquake']
        self.assertRegex(html, r'<div class="head"><h1 class="title">[^<]*</h1><div class="stamp">第 1 报</div></div>')
        self.assertNotIn('position:absolute', render.BULLETIN_STYLE)
        self.assertNotIn('overflow:hidden', render.BULLETIN_STYLE.split('.track')[0])

    def test_font_sizes_are_readable_at_900px(self):
        sizes = [int(n) for n in re.findall(r'font-size:(\d+)px', render.BULLETIN_STYLE)]
        self.assertGreaterEqual(min(sizes), 15)
        self.assertGreaterEqual(max(sizes), 60)

    def test_earthquake_hero_and_facts_do_not_repeat(self):
        html = cards()['earthquake']
        self.assertIn('<b class="stat-value">M5.2</b>', html)
        self.assertIn('最大烈度', html)
        self.assertIn('约 30 km', html)
        facts = html.split('<section class="meta">', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('报次', facts)          # 已在印章上
        self.assertNotIn('距本地', cards()['earthquake-character'])  # 未配置本地坐标

    def test_jma_quake_is_not_labelled_cenc(self):
        event = dataclasses.replace(logic.sample_event(), source='jma_eew', intensity='6弱')
        html = render.bulletin_html('earthquake', event, {})
        self.assertIn('日本气象厅 · 地震预警', html)
        self.assertNotIn('中国地震台网', html)
        self.assertIn('最大震度', html)

    def test_typhoon_track_uses_real_coordinates(self):
        html = cards()['typhoon']
        self.assertIn('aria-label="台风当前与预报位置"', html)
        for tick in ('105°E', '110°E', '115°E', '120°E', '20°N'):
            self.assertIn(f'>{tick}<', html)
        self.assertIn('预报移动约 1,300 km', html)
        self.assertIn('20.2°N 118.5°E', html)
        self.assertIn('西北 18 km/h', html)
        # 预报点在当前点西边：x 坐标更小
        now = re.search(r'class="t-now" cx="([\d.]+)"', html)
        future = re.search(r'class="t-future" cx="([\d.]+)"', html)
        self.assertLess(float(future.group(1)), float(now.group(1)))

    def test_typhoon_without_forecast_or_stopped_has_no_track(self):
        typhoon = dataclasses.replace(china.parse_eqsc_typhoon_item(EQSC_TYPHOON),
                                      future_latitude=None, future_longitude=None)
        self.assertNotIn('class="track"', render.bulletin_html('typhoon', typhoon, {}))
        stopped = cards()['typhoon-stopped']
        self.assertNotIn('class="track"', stopped)
        self.assertNotIn('class="hero"', stopped)
        self.assertIn('停编', stopped)

    def test_tsunami_stamp_names_the_warning(self):
        html = cards()['tsunami']
        self.assertIn('<div class="stamp">黄色警报</div>', html)
        self.assertIn('M7.8', html)

    def test_long_lead_is_bounded_with_notice(self):
        tsunami = china.parse_jian_tsunami(dict(JIAN_TSUNAMI, description='这是一段很长的海啸说明。' * 80))
        html = render.bulletin_html('tsunami', tsunami, {})
        if len(china.tsunami_lead(tsunami, 'neutral')) > render.LEAD_LIMIT:
            self.assertIn('完整内容见随附文字', html)
        lead = re.search(r'<p class="lead">(.*?)</p>', html, re.S).group(1)
        self.assertLessEqual(len(lead), render.LEAD_LIMIT)

    def test_text_is_escaped(self):
        event = dataclasses.replace(logic.sample_event(), place='<img src=x>')
        html = render.bulletin_html('earthquake', event, {})
        self.assertNotIn('<img src=x>', html)


class WholeCardRenderTest(unittest.TestCase):
    def capture(self, kind, item):
        seen = []

        def handler(request):
            if request.method != 'POST':  # 气象卡的行政边界请求：按不可用处理
                return httpx.Response(503)
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, content=png((900, 1400)), headers={'content-type': 'image/png'})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await render.render_bulletin(kind, item, {'render': {'enabled': True}}, 'neutral', client=client)
        result = asyncio.run(run())
        return result, seen

    def test_bulletin_cards_use_whole_card_capture(self):
        result, seen = self.capture('earthquake', logic.sample_event())
        self.assertTrue(result.startswith(b'\x89PNG'))
        self.assertEqual(seen[0][0], '/render-card')
        self.assertEqual(seen[0][1]['width'], 900)

    def test_weather_card_keeps_fixed_viewport(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        _, seen = self.capture('weather', alert)
        self.assertEqual(seen[-1][0], '/render')


if __name__ == '__main__':
    unittest.main()
