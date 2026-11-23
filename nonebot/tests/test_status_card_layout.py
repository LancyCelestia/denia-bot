"""Status card / daily report layout contract: pure HTML, no renderer needed (real screenshots in test_status_image)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import status_image as image
import status_logic as logic
from tests.test_status_image import view


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def report(sections):
    return {'title': '服务器日报', 'subtitle': '2026-11-21 · 昨日', 'host': 'h', 'sections': sections, 'footer': 'f'}


class StatusCardLayoutTest(unittest.TestCase):
    def test_short_path_keeps_the_distinguishing_tail(self):
        self.assertEqual(image.short_path('/'), '/')
        self.assertEqual(image.short_path('/mnt/storage/media/library'), '/mnt/storage/media/library')
        self.assertEqual(image.short_path('/mnt/storage/very-long-folder/media/library', 30), '/mnt/…/media/library')
        self.assertTrue(image.short_path('/var/lib/docker/overlay2/' + 'a' * 40).startswith('/var/…'))
        self.assertLessEqual(len(image.short_path('x' * 80)), 30)

    def test_disk_rows_use_short_paths(self):
        current = view()
        current['disks'][0]['path'] = '/mnt/storage/very-long-folder/media/library'
        html = image.build_status_html(current)
        self.assertIn('/mnt/…/media/library', html)
        self.assertIn('grid-template-columns:300px 1fr 205px 75px', html)

    def test_resource_cards_share_width_and_temperature_average_leaves_the_ring(self):
        current = view()
        current['resources'][0]['note'] = '78.6℃'
        current['resources'][0]['subnote'] = '60m 均 71.2℃'
        html = image.build_status_html(current)
        self.assertIn('.resource{flex:1 1 0;min-width:0', html)
        cpu = html.split('<section class="panel resource">')[1]
        ring, below = cpu.split('</svg>', 1)
        self.assertIn('78.6℃', ring)
        self.assertNotIn('60m 均', ring)
        self.assertRegex(below, r'<p class="detail">60m 均 71\.2℃ · ')
        self.assertNotIn('font-size="12"', html)

    def test_network_chart_states_its_time_window(self):
        current = view()
        current['network']['chart'] = [{'ts': 1763780000 + i * 60.0, 'up': i, 'down': i * 2} for i in range(60)]
        html = image.build_status_html(current)
        self.assertRegex(html, r'峰值 [^<]+ · \d\d:\d\d–\d\d:\d\d（近 59 分钟）')
        self.assertIn('vector-effect:non-scaling-stroke', html)

    def test_text_colours_meet_contrast_on_panels(self):
        for level, colour in image.TEXT_COLORS.items():
            with self.subTest(level):
                self.assertGreaterEqual(contrast(colour, '#fffcff'), 4.5)


class ReportLayoutTest(unittest.TestCase):
    def test_a_section_is_one_panel_unless_it_is_too_tall(self):
        html = image.build_report_html(report([
            {'title': '资源趋势', 'rows': [('CPU', '1%')] * 6,
             'chart': {'points': [(1763654400 + i * 300, 20.0 + i % 7) for i in range(288)], 'unit': '%', 'label': 'CPU'}},
            {'title': '异常汇总', 'rows': [('异常条数', '1')], 'table': {'headers': ('时间', '事件', '状态'), 'rows': [('11-21 03:00', '一条事件', '5 分 0 秒')]}},
        ]))
        self.assertNotIn('· 续', html)
        self.assertEqual(html.count('<section class="panel">'), 2)

        long = image.build_report_html(report([
            {'title': '异常汇总', 'table': {'headers': ('时间', '事件', '状态'), 'rows': [('11-21 03:00', '事件', '未恢复')] * 54}},
        ]))
        self.assertGreaterEqual(long.count('异常汇总 · 续'), 1)

    def test_percent_chart_uses_a_fixed_scale_and_time_axis(self):
        html = image.build_report_html(report([
            {'title': '资源趋势', 'chart': {'points': [(1763654400, 50.0), (1763654400 + 86100, 50.0)], 'unit': '%', 'label': 'CPU'}},
        ]))
        self.assertIn('0.00,52.50 1000.00,52.50', html)          # 50% 画在半高，而不是顶到最高
        self.assertIn('纵轴 0–100%', html)
        self.assertRegex(html, r'<div class="axis"><span>00:00</span><span>近 24 小时</span><span>23:55</span></div>')

    def test_tables_right_align_numbers_and_let_text_columns_wrap(self):
        html = image.build_report_html(report([
            {'title': '网站延迟', 'table': {'headers': ('站点', '可用率', '均值', '峰值'),
                                            'rows': [('网盘 (Nextcloud) 主站与备用节点', '50.0%', '1400ms', '1400ms')]}},
        ]))
        self.assertIn('<th>站点</th><th class="n">可用率</th>', html)
        self.assertIn('<td class="n">1400ms</td>', html)
        self.assertIn('.report-table{table-layout:auto}', html)

    def test_default_footer_no_longer_mentions_pillow(self):
        built = logic.build_report('daily', day='2026-11-21', days=1, host='h', sections=[])
        self.assertNotIn('Pillow', built['footer'])


if __name__ == '__main__':
    unittest.main()
