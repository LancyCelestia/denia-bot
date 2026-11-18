# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""渲染用例：PNG 产出、尺寸、脱敏与退化路径。

不断言像素内容（那种用例一改配色就红），只断言「出一张能看的图」需要的
不变量：格式、宽度、高度随内容变化、缺字段不崩、字体能找到。
"""

import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image

import status_image as image
import status_logic as logic


def view(**overrides):
    snapshot = {
        'uptime': 3600 * 30,
        'cpu': {'percent': 37.4, 'cores': 8, 'threads': 16, 'brand': 'Test CPU', 'freq_mhz': 3800},
        'memory': {'percent': 61.2, 'used': 9 * 1024 ** 3, 'total': 16 * 1024 ** 3},
        'swap': {'percent': 12.0, 'used': 512 * 1024 ** 2, 'total': 4 * 1024 ** 3},
        'disks': [
            {'path': '/', 'used': 412 * 1024 ** 3, 'total': 512 * 1024 ** 3, 'percent': 80.5},
            {'path': '/data', 'used': 180 * 1024 ** 3, 'total': 1024 ** 4, 'percent': 17.6},
        ],
        'disk_io': {'read_rate': 12 * 1024 ** 2, 'write_rate': 3 * 1024 ** 2},
        'network': {
            'up_rate': 1.2 * 1024 ** 2, 'down_rate': 8.4 * 1024 ** 2,
            'sent_total': 240 * 1024 ** 3, 'recv_total': 1024 ** 4,
            'month_up': 88 * 1024 ** 3, 'month_down': 420 * 1024 ** 3,
            'month_label': '10月', 'interface': 'eth0',
            'history': [{'ts': float(index), 'up': index * 1000.0, 'down': index * 4000.0} for index in range(30)],
        },
        'process': {
            'total': 312, 'running': 41,
            'top': [{'name': f'proc{index}', 'pid': index, 'cpu': 40 - index, 'mem': 10 - index} for index in range(8)],
        },
        'system': [
            {'label': '主机名', 'value': 'denia-prod'},
            {'label': '系统', 'value': 'Ubuntu 24.04 LTS'},
            {'label': '内核', 'value': '6.8.0'},
            {'label': 'Python', 'value': '3.12.3'},
            {'label': '开机', 'value': '2026-10-27 16:05'},
            {'label': '负载', 'value': '0.82 / 0.64 / 0.51'},
        ],
        'bot': {
            'nick': '达妮娅', 'self_id': '10000001', 'online': True,
            'protocol': 'SnowLuma', 'protocol_version': '1.3.2',
            'friends': 128, 'groups': 12, 'members': 4381, 'sent': 18420, 'recv': 27633,
        },
        'websites': [{'name': '博客', 'status': 'HTTP 200', 'delay_ms': 84, 'ok': True}],
    }
    snapshot.update(overrides)
    return logic.build_view(snapshot, now=1700000000)


def open_png(payload: bytes) -> Image.Image:
    return Image.open(BytesIO(payload))


def cluster_view(**overrides):
    snapshot = {
        'title': 'Proxmox VE',
        'generated_at': '2026-11-17 12:00:00',
        'source': 'Grafana',
        'summary': {'nodes': 2, 'guests': 2, 'running': 2, 'vms': 1, 'lxcs': 1, 'storages': 2},
        'nodes': [
            {
                'name': 'pve-a', 'cpu_percent': 37.4, 'cpu_text': '37.4%', 'cpu_level': 'good',
                'cpu_note': '60m 均 34.1%', 'cores': 16, 'load': '1.20 / 0.90 / 0.70',
                'memory_percent': 61.2, 'memory_text': '61.2%', 'memory_level': 'warn',
                'swap_percent': 0.0, 'swap_text': '0.0%', 'swap_level': 'good',
                'uptime': '12天 3小时', 'temperature_text': '42.3℃',
                'status': 'online', 'status_level': 'good',
            },
            {
                'name': 'pve-b', 'cpu_percent': 8.0, 'cpu_text': '8.0%', 'cpu_level': 'good',
                'cpu_note': '', 'cores': 8, 'load': '0.10 / 0.20 / 0.30',
                'memory_percent': None, 'memory_text': None, 'memory_level': '',
                'swap_percent': None, 'swap_text': None, 'swap_level': '',
                'uptime': '3天', 'temperature_text': '38.0℃',
                'status': 'online', 'status_level': 'good',
            },
        ],
        'guests': [
            {
                'vmid': 101, 'name': 'wiki', 'node': 'pve-a', 'type': 'qemu', 'type_label': 'VM',
                'status': 'running', 'status_label': '运行中', 'status_level': 'good',
                'cpu_percent': 12.5, 'cpu_text': '12.5%', 'cores': 2,
                'mem_text': '1.0 GiB / 4.0 GiB', 'mem_percent': 25.0, 'uptime': '3天 4小时',
            },
            {
                'vmid': 201, 'name': 'file', 'node': 'pve-b', 'type': 'lxc', 'type_label': 'LXC',
                'status': 'running', 'status_label': '运行中', 'status_level': 'good',
                'cpu_percent': 3.0, 'cpu_text': '3.0%', 'cores': 1,
                'mem_text': '512 MiB / 1.0 GiB', 'mem_percent': 50.0, 'uptime': '1天 2小时',
            },
        ],
        'storages': [
            {
                'name': 'local-lvm', 'node': 'pve-a', 'type': 'lvmthin', 'content': 'images,rootdir',
                'used_text': '120 GiB', 'total_text': '500 GiB', 'percent': 24.0,
                'percent_text': '24.0%', 'level': 'good', 'active': True,
            },
            {
                'name': 'backup', 'node': 'pve-b', 'type': 'dir', 'content': 'backup',
                'used_text': '780 GiB', 'total_text': '1000 GiB', 'percent': 78.0,
                'percent_text': '78.0%', 'level': 'warn', 'active': False,
            },
        ],
    }
    snapshot.update(overrides)
    return snapshot


class StatusCardTest(unittest.TestCase):
    def test_cpu_temperature_note_renders_inside_ring(self):
        current = view()
        current['resources'][0]['note'] = '42.3℃'
        current['resources'][0]['subnote'] = '60m 均 40.1℃'
        html = image.build_status_html(current)
        self.assertIn('42.3℃', html)
        self.assertIn('60m 均 40.1℃', html)
        self.assertNotIn('℃', current['resources'][0]['detail'])

    def test_website_readings_are_visible_and_have_space(self):
        current = view()
        baseline = image.render_status_card(current)
        without_sites = image.render_status_card(view(websites=[]))
        self.assertGreater(open_png(baseline).height, open_png(without_sites).height)
        for field, changed in [('name', 'API'), ('status', 'HTTP 503'),
                               ('delay', '920ms'), ('ok', False)]:
            with self.subTest(field=field):
                changed_view = view()
                changed_view['websites'][0][field] = changed
                self.assertNotEqual(image.render_status_card(changed_view), baseline)

    def test_renders_png_with_requested_width(self):
        png = image.render_status_card(view(), width=1170)
        self.assertTrue(png.startswith(b'\x89PNG'))
        rendered = open_png(png)
        self.assertEqual(rendered.format, 'PNG')
        self.assertEqual(rendered.width, 1170)
        self.assertGreater(rendered.height, 900)

    def test_width_is_clamped(self):
        narrow = open_png(image.render_status_card(view(), width=100))
        self.assertEqual(narrow.width, 760, '过窄的宽度会被夹到下限')
        wide = open_png(image.render_status_card(view(), width=99999))
        self.assertEqual(wide.width, 2880, '过宽的宽度会被夹到上限')

    def test_compact_is_shorter(self):
        normal = open_png(image.render_status_card(view(), width=1170))
        compact = open_png(image.render_status_card(view(compact=True), width=1170))
        self.assertLess(compact.height, normal.height)

    def test_redaction_cannot_leak_hostname(self):
        redacted = view()
        logic.apply_redaction(redacted, ['hostname', 'process'])
        png = image.render_status_card(redacted)
        self.assertTrue(png.startswith(b'\x89PNG'))
        # 渲染层拿到的就是脱敏后的视图：这里直接断言数据，不靠图像识别
        self.assertEqual(redacted['system'][0]['value'], logic.HIDDEN)
        self.assertEqual(redacted['process']['top'][0]['name'], logic.HIDDEN)

    def test_missing_optional_sections_do_not_crash(self):
        bare = view(
            disks=[],
            websites=[],
            process={'total': None, 'running': None, 'top': []},
            system=[],
            network={'up_rate': 0, 'down_rate': 0, 'history': []},
        )
        png = image.render_status_card(bare)
        self.assertGreater(open_png(png).height, 400)

    def test_no_history_renders_note(self):
        without_chart = view(network={
            'up_rate': 0, 'down_rate': 0, 'month_label': '10月', 'history': [],
        })
        png = image.render_status_card(without_chart)
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_broken_avatar_bytes_fall_back_to_placeholder(self):
        broken = view()
        broken['bot']['avatar'] = b'not an image'
        self.assertTrue(image.render_status_card(broken).startswith(b'\x89PNG'))

    def test_bundled_background_changes_header_without_changing_data_panels(self):
        rendered = open_png(image.render_status_card(view()))
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            with patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True):
                fallback = open_png(image.render_status_card(view()))
        self.assertEqual(rendered.size, fallback.size)
        self.assertTrue(rendered.crop((0, 0, 1170, 500)).tobytes() !=
                        fallback.crop((0, 0, 1170, 500)).tobytes(), '背景应出现在顶部')
        self.assertEqual(rendered.crop((0, 1000, 1170, rendered.height)).tobytes(),
                         fallback.crop((0, 1000, 1170, fallback.height)).tobytes())

    def test_missing_and_corrupt_backgrounds_both_render_the_plain_fallback(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            target = Path(tmp) / 'background.jpg'
            with patch.object(image, '_BACKGROUND_FILE', target, create=True):
                missing = image.render_status_card(view())
                target.write_bytes(b'not a jpeg')
                corrupt = image.render_status_card(view())
        self.assertTrue(missing.startswith(b'\x89PNG'))
        self.assertEqual(missing, corrupt)

    def test_background_loads_independently_of_working_directory(self):
        expected = image.render_status_card(view())
        previous = Path.cwd()
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            try:
                os.chdir(tmp)
                actual = image.render_status_card(view())
            finally:
                os.chdir(previous)
        self.assertEqual(expected, actual)

    def test_requested_width_scales_the_whole_layout(self):
        standard = open_png(image.render_status_card(view()))
        for width in (760, 2880):
            scaled = open_png(image.render_status_card(view(), width=width))
            self.assertEqual(scaled.width, width)
            self.assertAlmostEqual(scaled.height / standard.height, width / 1170, delta=.001)


class ClusterCardTest(unittest.TestCase):
    def test_html_contains_nodes_guests_and_storages(self):
        html = image.build_cluster_html(cluster_view())
        for piece in ('PROXMOX VE / CLUSTER STATUS', 'Proxmox VE', '2026-11-17 12:00:00',
                      'pve-a', 'pve-b', 'wiki', '#101', 'VM', 'LXC',
                      'local-lvm', 'backup', '60m 均 34.1%', '38.0℃', '数据 Grafana · 本地渲染'):
            self.assertIn(piece, html)
        self.assertNotIn('未获取到节点数据', html)

    def test_memory_pair_text_sits_below_the_ring(self):
        node = dict(cluster_view()['nodes'][0], memory_percent=63.0, memory_text='9.08 GB / 14.42 GB')
        html = image.build_cluster_html(cluster_view(nodes=[node], guests=[], storages=[]))
        self.assertIn('<p class="detail">9.08 GB / 14.42 GB</p>', html)
        self.assertIn('63.0%', html, '环心应显示百分比')
        for fragment in html.split('<svg')[1:]:
            self.assertNotIn('9.08 GB / 14.42 GB', fragment.split('</svg>')[0], '配对文本不应挤进资源环')

    def test_temperature_is_rendered_once(self):
        node = dict(cluster_view()['nodes'][0], cpu_note='57.8℃', temperature_text='57.8℃')
        html = image.build_cluster_html(cluster_view(nodes=[node], guests=[], storages=[]))
        self.assertEqual(html.count('57.8℃'), 1, 'cpu_note 与 temperature_text 只应渲染一行')

    def test_renders_png_with_requested_width(self):
        png = image.render_cluster_card(cluster_view(), width=1170)
        self.assertTrue(png.startswith(b'\x89PNG'))
        rendered = open_png(png)
        self.assertEqual(rendered.format, 'PNG')
        self.assertEqual(rendered.width, 1170)
        self.assertGreater(rendered.height, 600)

    def test_width_is_clamped(self):
        narrow = open_png(image.render_cluster_card(cluster_view(), width=100))
        self.assertEqual(narrow.width, 760, '过窄的宽度会被夹到下限')
        wide = open_png(image.render_cluster_card(cluster_view(), width=99999))
        self.assertEqual(wide.width, 2880, '过宽的宽度会被夹到上限')

    def test_empty_guests_and_storages_still_render(self):
        html = image.build_cluster_html(cluster_view(guests=[], storages=[]))
        self.assertIn('没有访客数据', html)
        self.assertIn('没有存储数据', html)
        png = image.render_cluster_card(cluster_view(guests=[], storages=[]))
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_missing_optional_keys_do_not_crash(self):
        sparse = {
            'summary': {},
            'nodes': [{'name': 'pve-only'}],
            'guests': [{'vmid': 300}],
            'storages': [{'name': 'store'}],
        }
        html = image.build_cluster_html(sparse)
        self.assertIn('--', html)
        self.assertIn('没有节点数据', image.build_cluster_html({}))
        self.assertTrue(image.render_cluster_card(sparse).startswith(b'\x89PNG'))


class ReportImageTest(unittest.TestCase):
    def report(self, **overrides):
        payload = {
            'kind': 'daily',
            'title': '服务器日报',
            'subtitle': '2026-10-30 · 昨日',
            'host': 'denia-prod',
            'sections': [
                {'key': 'resource', 'title': '资源趋势', 'rows': [('CPU 均值 / 峰值', '36.2% / 59.0%')],
                 'chart': {'points': [(1, 10.0), (2, 20.0), (3, 15.0)], 'unit': '%', 'label': 'CPU'}},
                {'key': 'qq', 'title': 'QQ 统计', 'rows': [('好友', '128'), ('昨日接收', '633')]},
                {'key': 'website', 'title': '网站延迟',
                 'table': {'headers': ('站点', '可用率'), 'rows': [('博客', '100.0%'), ('网盘', '0.0%')]}},
            ],
            'footer': '本地 Pillow 渲染',
        }
        payload.update(overrides)
        return payload

    def test_renders_png(self):
        png = image.render_report(self.report())
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(open_png(png).width, 1170)

    def test_more_sections_are_taller(self):
        short = image.render_report_pages(self.report())
        tall = image.render_report_pages(self.report(sections=self.report()['sections'] * 3))
        self.assertGreater(sum(open_png(page).height for page in tall), sum(open_png(page).height for page in short))

    def test_empty_sections_still_renders(self):
        png = image.render_report(self.report(sections=[]))
        self.assertTrue(png.startswith(b'\x89PNG'))


class BannerBackgroundTest(unittest.TestCase):
    """集群卡与日报 / 周报的顶部横幅从资源集合里随机取，状态卡主视觉固定。"""

    def make_image(self, folder, name, color):
        path = Path(folder) / name
        Image.new('RGB', (48, 27), color).save(path, 'JPEG')
        return path

    def test_cards_pick_from_the_banner_set(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            banners = Path(tmp) / 'banners'
            banners.mkdir()
            blue = self.make_image(banners, 'blue.jpg', (10, 40, 200))
            red = self.make_image(banners, 'red.jpg', (200, 30, 30))
            with patch.object(image, '_BANNER_DIR', banners), \
                    patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True):
                expected = {image._art(image.image_uri(path)) for path in (blue, red)}
                picked = set()
                for _ in range(40):
                    html = image.build_cluster_html(cluster_view())
                    self.assertIn('class="panel cluster-head banner"', html)
                    art = html.split('<div class="art" style="')[1].split('"></div>')[0]
                    self.assertIn('data:image/jpeg;base64,', art)
                    picked.add('<div class="art" style="' + art + '"></div>')
                self.assertEqual(picked, expected, '两张图都应有机会被选中')
                self.assertEqual(image.build_report_html(ReportImageTest().report()).count('class="art"'), 1)

    def test_broken_candidate_is_skipped(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            banners = Path(tmp) / 'banners'
            banners.mkdir()
            (banners / 'a-broken.jpg').write_bytes(b'not a jpeg')
            good = self.make_image(banners, 'b-good.jpg', (20, 160, 90))
            with patch.object(image, '_BANNER_DIR', banners), \
                    patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True), \
                    patch.object(image.random, 'shuffle', lambda items: None):
                html = image.build_cluster_html(cluster_view())
            self.assertIn(image._art(image.image_uri(good)), html)

    def test_no_candidates_falls_back_to_flat_colour(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            empty = Path(tmp) / 'banners'
            empty.mkdir()
            with patch.object(image, '_BANNER_DIR', empty), \
                    patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True):
                self.assertEqual(image.banner_files(), [])
                self.assertEqual(image.banner_art(), '')
                self.assertTrue(image.render_cluster_card(cluster_view()).startswith(b'\x89PNG'))

    def test_status_hero_keeps_the_chosen_asset(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            banners = Path(tmp) / 'banners'
            banners.mkdir()
            self.make_image(banners, 'other.jpg', (200, 30, 30))
            hero = self.make_image(Path(tmp), 'hero.jpg', (10, 40, 200))
            with patch.object(image, '_BANNER_DIR', banners), \
                    patch.object(image, '_BACKGROUND_FILE', hero, create=True):
                html = image.build_status_html(view())
            self.assertIn(image._art(image.image_uri(hero)), html)
            self.assertNotIn(image._art(image.image_uri(banners / 'other.jpg')), html)

    def test_banner_changes_only_the_top_of_the_card(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            empty = Path(tmp) / 'banners'
            empty.mkdir()
            with patch.object(image, '_BANNER_DIR', empty), \
                    patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True):
                plain = tuple(open_png(png) for png in (image.render_cluster_card(cluster_view()),
                                                        image.render_report(ReportImageTest().report())))
        rendered = (open_png(image.render_cluster_card(cluster_view())),
                    open_png(image.render_report(ReportImageTest().report())))
        for full, bare in zip(rendered, plain):
            self.assertEqual(full.size, bare.size, '横幅预留高度，缺素材时布局不变')
            self.assertNotEqual(full.crop((0, 0, 1170, 300)).tobytes(),
                                bare.crop((0, 0, 1170, 300)).tobytes(), '背景应出现在顶部')
            self.assertEqual(full.crop((0, 460, 1170, full.height)).tobytes(),
                             bare.crop((0, 460, 1170, bare.height)).tobytes())


if __name__ == '__main__':
    unittest.main()
