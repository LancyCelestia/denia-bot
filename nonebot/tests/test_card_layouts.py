"""Pure HTML tests for the redesigned weather / group topics / Epic cards: no renderer needed, runs offline in seconds.

Real screenshots are still covered by test_weather_image.py / test_epic_free_image.py (they need QQBOT_RENDER_URL).
"""
import asyncio
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from PIL import Image

import epic_free_image
import epic_free_logic as logic
import epic_free_service as service
import group_topics_image
import weather_image
import weather_service

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
NOW = datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc)
BEIJING = timezone(timedelta(hours=8))


def elements(name='epic_free_cn.json'):
    return json.loads((FIXTURES / name).read_text('utf-8'))['data']['Catalog']['searchStore']['elements']


def png(size=(64, 36)):
    out = BytesIO()
    Image.new('RGB', size, '#8a6').save(out, 'PNG')
    return out.getvalue()


def card_blocks(html):
    """data-card 根的直接子元素就是分页块：根必须唯一，且不能有脚本或外链。"""
    assert html.count('data-card') == 1, 'exactly one data-card root'
    assert '<script' not in html
    assert not re.search(r'(?:src|href)="https?://', html), 'no external resources'
    return html


def lookup_then(payload):
    def handler(request):
        if request.url.path.endswith('/city/lookup'):
            return httpx.Response(200, json={'location': [{'name': '西湖', 'adm1': '浙江省', 'adm2': '杭州市', 'lat': '30.25', 'lon': '120.13'}]})
        return httpx.Response(200, json=payload)
    return handler


def fetch(payload, forecast, now=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lookup_then(payload))) as client:
            return await weather_service.fetch_view('abc.qweatherapi.com', 'k', '西湖', forecast, client, now=now)
    return asyncio.run(run())


FORECAST = {'days': [
    {'forecastStartTime': '2026-11-22T00:00+08:00', 'daytime': {'condition': {'text': '多云'}}, 'temperatureMin': {'value': 9}, 'temperatureMax': {'value': 16}},
    {'forecastStartTime': '2026-11-23T00:00+08:00', 'daytime': {'condition': {'text': '小雨'}}, 'temperatureMin': {'value': 8}, 'temperatureMax': {'value': 13}},
    {'forecastStartTime': '2026-11-24T00:00+08:00', 'daytime': {'condition': {'text': '晴'}}, 'temperatureMin': {'value': 4}, 'temperatureMax': {'value': 11}},
]}
CURRENT = {'current': {'condition': {'text': '多云'}, 'temperature': {'value': 12}, 'feelsLike': {'value': 10}}}
TODAY = datetime(2026, 11, 22, 11, 0, tzinfo=BEIJING)


class WeatherViewTest(unittest.TestCase):
    def test_text_fallback_is_unchanged(self):
        view = fetch(FORECAST, True, TODAY)
        self.assertEqual(weather_service.format_text(view),
                         '浙江省 杭州市 西湖\n2026-11-22 多云 9～16℃\n2026-11-23 小雨 8～13℃\n2026-11-24 晴 4～11℃\n'
                         + weather_service.ATTRIBUTION)
        now = fetch(CURRENT, False, TODAY)
        self.assertEqual(weather_service.format_text(now).splitlines()[1], '多云 12℃，体感 10℃')

    def test_forecast_labels_relative_to_today(self):
        view = fetch(FORECAST, True, TODAY)
        self.assertEqual([d['label'] for d in view['days']], ['今天', '明天', '后天'])
        self.assertEqual(view['days'][2]['short'], '11/24')
        self.assertEqual(view['queried_at'], '11月22日 周日 11:00')

    def test_missing_values_do_not_crash(self):
        view = fetch({'current': {'condition': {'text': '晴'}}}, False, TODAY)
        self.assertIsNone(view['temperature'])
        self.assertEqual(view['facts'], [])
        html = card_blocks(weather_image.build_html(view))
        self.assertIn('<section class="panel now missing"><div class="temp missing">--</div>', html)
        self.assertIn('?℃', weather_service.format_text(view))

    def test_optional_facts_only_when_present(self):
        payload = {'current': dict(CURRENT['current'], relativeHumidity={'value': 68},
                                   wind={'direction': {'text': '东北风'}, 'scale': {'value': 2}})}
        view = fetch(payload, False, TODAY)
        self.assertEqual(view['facts'], [('湿度', '68%'), ('风', '东北风 2 级')])
        self.assertIn('东北风 2 级', weather_image.build_html(view))


class WeatherCardTest(unittest.TestCase):
    def test_now_card_leads_with_temperature(self):
        html = card_blocks(weather_image.build_html(fetch(CURRENT, False, TODAY)))
        self.assertIn('<section class="panel now"><div class="temp">12<span class="deg">°', html)
        self.assertIn('体感 10°，比实际低 2°', html)
        self.assertIn('.天气预报 西湖', html)

    def test_forecast_card_lead_and_shared_scale(self):
        view = fetch(FORECAST, True, TODAY)
        html = card_blocks(weather_image.build_html(view))
        self.assertIn('后天最低 4°，比今天低 5°', html)
        self.assertIn('共用刻度 4°～16°', html)
        # 最冷那天的条从 0% 开始，最热的到 100%
        self.assertIn('left:0.0%;width:58.3%', html)
        self.assertIn('left:41.7%;width:58.3%', html)

    def test_feels_text(self):
        self.assertEqual(weather_image.feels_text(12, 12), '体感 12°，和实际一致')
        self.assertEqual(weather_image.feels_text(30, 33), '体感 33°，比实际高 3°')
        self.assertEqual(weather_image.feels_text(None, 8), '体感 8°')

    def test_today_is_coldest(self):
        days = [{'label': '今天', 'min': 2, 'max': 9}, {'label': '明天', 'min': 5, 'max': 12}]
        self.assertEqual(weather_image.forecast_lead(days), '今天最冷，2 天气温 2°～12°')

    def test_legacy_text_input_still_builds(self):
        html = card_blocks(weather_image.build_html('上海\n晴 21℃，体感 20℃\n数据来源：和风天气'))
        self.assertIn('上海', html)
        self.assertIn('class="sheet light"', html)

    def test_text_is_escaped(self):
        view = fetch(CURRENT, False, TODAY)
        view['place'] = '<b>x</b>'
        self.assertNotIn('<b>x</b>', weather_image.build_html(view))


class GroupTopicsCardTest(unittest.TestCase):
    ROWS = [('原神', 126), ('期末', 98), ('火锅', 73), ('猫', 4)]

    def test_headline_states_the_finding(self):
        html = card_blocks(group_topics_image.build_html('123', self.ROWS))
        self.assertIn('「原神」聊得最多，126 条消息提到', html)
        self.assertIn('群 123 · 最近约 7 天', html)
        self.assertEqual(html.count('class="r top"'), 3)
        self.assertIn('width:100.0%', html)

    def test_empty_rows_rejected_and_words_escaped(self):
        with self.assertRaises(ValueError):
            group_topics_image.build_html('1', [])
        self.assertNotIn('<img', group_topics_image.build_html('1', [('<img>', 3)]))


class EpicCardTest(unittest.TestCase):
    def setUp(self):
        self.view = logic.build_view(elements(), NOW)

    def test_cover_url_prefers_wide_epic_cdn(self):
        element = {'keyImages': [
            {'type': 'Thumbnail', 'url': 'https://cdn1.epicgames.com/t.jpg'},
            {'type': 'OfferImageWide', 'url': 'https://evil.example.com/w.jpg'},
            {'type': 'OfferImageWide', 'url': 'https://cdn1.epicgames.com/w.jpg'},
        ]}
        self.assertEqual(logic.cover_url(element), 'https://cdn1.epicgames.com/w.jpg')
        self.assertEqual(logic.cover_url({'keyImages': [{'type': 'OfferImageWide', 'url': 'http://cdn1.epicgames.com/a'}]}), '')
        self.assertTrue(all(game['cover_url'].startswith('https://') for game in self.view['free']))

    def test_remaining_text(self):
        game = {'end': NOW + timedelta(days=4, hours=3)}
        self.assertEqual(logic.remaining_text([game], NOW), '还剩 4 天')
        self.assertEqual(logic.remaining_text([{'end': NOW + timedelta(hours=5)}], NOW), '还剩 5 小时')
        self.assertEqual(logic.remaining_text([], NOW), '')
        self.assertEqual(logic.format_short(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)), '11月27日 周五 00:00')

    def test_card_embeds_covers_and_keeps_links_out(self):
        covers = {game['cover_url']: png() for game in self.view['free'] + self.view['upcoming']}
        html = card_blocks(epic_free_image.build_html(self.view['free'], self.view['upcoming'], covers=covers, now=NOW))
        self.assertIn('data:image/jpeg;base64,', html)
        self.assertNotIn('store.epicgames.com', html)
        self.assertIn(f'本周 {len(self.view["free"])} 款免费', html)
        self.assertIn('领取链接见下一条文字消息', html)

    def test_missing_covers_and_empty_state(self):
        html = card_blocks(epic_free_image.build_html(self.view['free'], self.view['upcoming'], now=NOW))
        self.assertNotIn('<img', html)
        empty = card_blocks(epic_free_image.build_html([], []))
        self.assertIn('本周没有免费游戏', empty)

    def test_request_stays_under_renderer_budget(self):
        games = [dict(self.view['free'][0], id=str(i), cover_url=f'https://cdn1.epicgames.com/{i}.jpg') for i in range(6)]
        noisy = Image.effect_noise((2560, 1440), 80).convert('RGB')
        out = BytesIO(); noisy.save(out, 'JPEG', quality=95)
        covers = {game['cover_url']: out.getvalue() for game in games}
        html = epic_free_image.build_html(games, [], covers=covers, now=NOW)
        body = json.dumps({'html': html, 'width': 760, 'height': 2400, 'timeout_ms': 20000}, ensure_ascii=False).encode()
        self.assertLess(len(body), 2 * 1024 * 1024)
        self.assertEqual(html.count('<img class="cover"'), epic_free_image.MAX_LARGE_COVERS)


class EpicCoverDownloadTest(unittest.TestCase):
    def run_covers(self, handler, games):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await service.fetch_covers(games, client=client)
        return asyncio.run(run())

    def test_downloads_allowed_images_only(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            if request.url.path == '/big.jpg':
                return httpx.Response(200, headers={'content-type': 'image/jpeg'}, content=b'x' * (service.COVER_MAX_BYTES + 1))
            if request.url.path == '/page.jpg':
                return httpx.Response(200, headers={'content-type': 'text/html'}, content=b'<html>')
            return httpx.Response(200, headers={'content-type': 'image/png'}, content=png())

        games = [{'cover_url': 'https://cdn1.epicgames.com/ok.png'},
                 {'cover_url': 'https://cdn1.epicgames.com/big.jpg'},
                 {'cover_url': 'https://cdn1.epicgames.com/page.jpg'},
                 {'cover_url': 'https://evil.example.com/x.png'},
                 {'cover_url': ''}]
        result = self.run_covers(handler, games)
        self.assertEqual(list(result), ['https://cdn1.epicgames.com/ok.png'])
        self.assertFalse(any('evil.example.com' in url for url in seen))

    def test_errors_become_missing_covers(self):
        def handler(request):
            raise httpx.ConnectError('down')
        self.assertEqual(self.run_covers(handler, [{'cover_url': 'https://cdn1.epicgames.com/a.jpg'}]), {})


if __name__ == '__main__':
    unittest.main()
