# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""Proxmox VE 集群数据层用例。

覆盖三件事：Flux 查询拼装（含 bucket 校验）、Grafana frame 的标签合并与
按 host/vmid 归一化、以及视图格式化。全部离线，没有 Grafana 也没有集群。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_cluster as cluster
import status_config as config_mod
import status_grafana as grafana


GRAFANA_CONFIG = {
    'enabled': True,
    'url': 'http://grafana.local:3000/path/d/abc?orgId=1',
    'datasource_uid': 'influx-uid',
    'timeout_seconds': 5,
}

CLUSTER_CONFIG = {
    'enabled': True,
    'title': 'Proxmox VE',
    'bucket': 'proxmox',
    'datasource_uid': '',
    'window_minutes': 10,
    'node_filter': [],
    'show_temperature': True,
    'cache_seconds': 15,
}

NODE_KEYS = {
    'name', 'cpu_percent', 'cpu_text', 'cpu_level', 'cpu_note', 'cores', 'load',
    'memory_percent', 'memory_text', 'memory_level', 'swap_percent', 'swap_text',
    'swap_level', 'uptime', 'temperature_text', 'status', 'status_level',
}
GUEST_KEYS = {
    'vmid', 'name', 'node', 'type', 'type_label', 'status', 'status_label',
    'status_level', 'cpu_percent', 'cpu_text', 'cores', 'mem_text', 'mem_percent',
    'uptime',
}
STORAGE_KEYS = {
    'name', 'node', 'type', 'content', 'used_text', 'total_text', 'percent',
    'percent_text', 'level', 'active',
}


class FakeResponse:
    def __init__(self, payload, content_type='application/json', status=200):
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        self.headers = {'Content-Type': content_type}
        self.status = status

    def read(self, size=-1):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def frame(fields, values):
    return {'schema': {'fields': fields}, 'data': {'values': values}}


def series(name, labels, *values):
    """一帧单字段序列；`values` 是 (ts, value) 元组。"""
    return frame(
        [
            {'name': 'Time'},
            {'name': name, 'labels': labels},
        ],
        [[ts for ts, _ in values], [value for _, value in values]],
    )


def query_payload(frames_by_ref):
    return {'results': {
        ref: {'status': 200, 'frames': frames} for ref, frames in frames_by_ref.items()
    }}


class BuildQueriesTest(unittest.TestCase):
    def test_queries_cover_every_measurement_and_window(self):
        queries = cluster.build_queries('proxmox', 10)
        self.assertEqual(sorted(queries), ['A', 'B', 'C', 'D', 'E', 'F'])
        for text in queries.values():
            self.assertIn('from(bucket: "proxmox")', text)
            self.assertIn('|> range(start: -10m)', text)
            self.assertTrue(text.endswith('|> last()'))
        self.assertIn('r["_measurement"] == "cpustat"', queries['A'])
        self.assertIn('r["object"] == "nodes"', queries['A'])
        for field in ('cpu', 'cpus', 'avg1', 'avg5', 'avg15'):
            self.assertIn(f'r["_field"] == "{field}"', queries['A'])
        self.assertIn('r["_measurement"] == "memory"', queries['B'])
        self.assertIn('r["_field"] == "swaptotal"', queries['B'])
        self.assertIn('r["_field"] == "uptime"', queries['C'])
        self.assertIn('r["object"] == "qemu"', queries['D'])
        self.assertIn('r["object"] == "lxc"', queries['D'])
        for field in ('name', 'status', 'cpu', 'cpus', 'mem', 'maxmem', 'uptime'):
            self.assertIn(f'r["_field"] == "{field}"', queries['D'])
        self.assertIn('r["object"] == "storages"', queries['E'])
        self.assertIn('r["_field"] == "active"', queries['E'])
        self.assertIn('r["_measurement"] == "hwmon"', queries['F'])
        self.assertIn('r["_field"] == "temp_celsius"', queries['F'])

    def test_temperature_query_is_optional(self):
        queries = cluster.build_queries('proxmox', 10, include_temperature=False)
        self.assertNotIn('F', queries)
        self.assertEqual(sorted(queries), ['A', 'B', 'C', 'D', 'E'])

    def test_window_is_clamped_and_never_injected(self):
        self.assertIn('range(start: -1m)', cluster.build_queries('proxmox', 0)['A'])
        self.assertIn('range(start: -1440m)', cluster.build_queries('proxmox', 99999)['A'])
        self.assertIn('range(start: -10m)', cluster.build_queries('proxmox', 'oops')['A'])

    def test_invalid_bucket_is_rejected(self):
        for bad in ('', '   ', 'pve prod', 'proxmox" |> drop()', 'a' * 65):
            with self.assertRaises(grafana.GrafanaError):
                cluster.build_queries(bad, 10)

    def test_bucket_allows_dots_dashes_and_underscores(self):
        queries = cluster.build_queries('pve.prod_1-2', 5)
        self.assertIn('from(bucket: "pve.prod_1-2")', queries['A'])


class QueryFluxBatchTest(unittest.TestCase):
    def run_batch(self, payload, queries=None, config=None, token='glsa_test', **kwargs):
        response = FakeResponse(payload)
        with patch.object(grafana._OPENER, 'open', return_value=response) as urlopen:
            result = grafana.query_flux_batch(
                config or GRAFANA_CONFIG,
                queries or {'A': 'from(bucket: "proxmox")'},
                token=token,
                **kwargs,
            )
        return result, urlopen

    def test_post_body_and_headers(self):
        payload = query_payload({'A': [series('cpu', {'host': 'pve'}, (100, 0.25))]})
        result, urlopen = self.run_batch(payload, queries={'A': 'flux-a', 'B': 'flux-b'})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'http://grafana.local:3000/path/api/ds/query')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer glsa_test')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(body['from'], '0')
        self.assertEqual(body['to'], '0')
        self.assertEqual([item['refId'] for item in body['queries']], ['A', 'B'])
        first = body['queries'][0]
        self.assertEqual(first['datasource'], {'type': 'influxdb', 'uid': 'influx-uid'})
        self.assertEqual(first['query'], 'flux-a')
        self.assertTrue(first['rawQuery'])
        self.assertEqual(first['resultFormat'], 'time_series')
        self.assertEqual(first['editorMode'], 'code')
        self.assertEqual(result, {
            'A': [{'host': 'pve', 'Time': 100, 'cpu': 0.25}], 'B': [],
        })

    def test_timeout_is_clamped(self):
        payload = query_payload({'A': []})
        _, urlopen = self.run_batch(payload, timeout=99)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 15)
        _, urlopen = self.run_batch(payload, timeout=1)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 3)
        _, urlopen = self.run_batch(payload, config={**GRAFANA_CONFIG, 'timeout_seconds': 7})
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 7)

    def test_field_names_win_over_labels_and_later_labels_win(self):
        payload = query_payload({'A': [frame(
            [
                {'name': 'host', 'labels': {'host': 'label-host', 'object': 'nodes'}},
                {'name': 'cpu', 'labels': {'host': 'second-host'}},
            ],
            [['field-host'], [0.5]],
        )]})
        result, _ = self.run_batch(payload)
        self.assertEqual(result['A'], [{
            'object': 'nodes', 'host': 'field-host', 'cpu': 0.5,
        }])

    def test_labels_are_merged_per_row(self):
        payload = query_payload({'A': [frame(
            [
                {'name': 'Time'},
                {'name': 'cpu', 'labels': {'host': 'pve', 'object': 'nodes'}},
                {'name': 'cpus', 'labels': {'host': 'pve', 'object': 'nodes'}},
            ],
            [[100, 160], [0.1, 0.2], [16, 16]],
        )]})
        result, _ = self.run_batch(payload)
        self.assertEqual(result['A'], [
            {'host': 'pve', 'object': 'nodes', 'Time': 100, 'cpu': 0.1, 'cpus': 16},
            {'host': 'pve', 'object': 'nodes', 'Time': 160, 'cpu': 0.2, 'cpus': 16},
        ])

    def test_empty_frames_give_empty_lists(self):
        payload = query_payload({'A': [frame([{'name': 'Time'}], [[100]])], 'B': []})
        result, _ = self.run_batch(payload, queries={'A': 'x', 'B': 'y', 'C': 'z'})
        self.assertEqual(result, {'A': [{'Time': 100}], 'B': [], 'C': []})

    def test_ref_error_is_raised_and_recorded(self):
        payload = {'results': {'A': {'status': 500, 'error': 'boom', 'frames': []}}}
        with self.assertRaises(grafana.GrafanaError):
            self.run_batch(payload)
        self.assertIn('boom', grafana.last_error())
        self.assertIn('A', grafana.last_error())

    def test_http_error_is_raised(self):
        import io
        import urllib.error
        failure = urllib.error.HTTPError(
            'http://grafana.local', 401, 'Unauthorized', {},
            io.BytesIO(b'{"message":"Invalid API key"}'),
        )
        with patch.object(grafana._OPENER, 'open', side_effect=failure):
            with self.assertRaises(grafana.GrafanaError):
                grafana.query_flux_batch(GRAFANA_CONFIG, {'A': 'x'}, token='glsa_test')
        self.assertIn('401', grafana.last_error())
        self.assertIn('Invalid API key', grafana.last_error())

    def test_non_json_response_is_raised(self):
        response = FakeResponse(b'<html>Not Found</html>', content_type='text/html', status=404)
        with patch.object(grafana._OPENER, 'open', return_value=response):
            with self.assertRaises(grafana.GrafanaError):
                grafana.query_flux_batch(GRAFANA_CONFIG, {'A': 'x'}, token='glsa_test')
        self.assertIn('非 JSON', grafana.last_error())

    def test_missing_uid_and_token_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(grafana.GrafanaError):
                grafana.query_flux_batch({**GRAFANA_CONFIG, 'datasource_uid': ''}, {'A': 'x'},
                                         root=tmp, token='glsa_test')
            self.assertIn('UID', grafana.last_error())
            with self.assertRaises(grafana.GrafanaError):
                grafana.query_flux_batch(GRAFANA_CONFIG, {'A': 'x'}, root=tmp)
            self.assertIn('API Key', grafana.last_error())

    def test_invalid_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(grafana.GrafanaError):
                grafana.query_flux_batch({'url': 'file:///etc/passwd', 'datasource_uid': 'u'},
                                         {'A': 'x'}, root=tmp, token='glsa_test')
        self.assertIn('http', grafana.last_error())

    def test_empty_query_map_skips_network(self):
        with patch.object(grafana._OPENER, 'open') as urlopen:
            self.assertEqual(grafana.query_flux_batch(GRAFANA_CONFIG, {}, token='x'), {})
        urlopen.assert_not_called()


class NormalizeTest(unittest.TestCase):
    def test_node_merges_fields_and_computes_ratios(self):
        results = {
            'A': [
                {'host': 'pve', 'cpu': 0.25, 'cpus': 16},
                {'host': 'pve', 'avg1': 0.5, 'avg5': 0.4, 'avg15': 0.3},
            ],
            'B': [
                {'host': 'pve', 'memused': 4 * 1024 ** 3},
                {'host': 'pve', 'memtotal': 16 * 1024 ** 3},
                {'host': 'pve', 'swapused': 1024 ** 3},
                {'host': 'pve', 'swaptotal': 4 * 1024 ** 3},
            ],
            'C': [{'host': 'pve', 'uptime': 3600 * 30}],
            'F': [
                {'host': 'pve', 'temp_celsius': 52.0, 'chip_name': 'nvme'},
                {'host': 'pve', 'temp_celsius': 45.5, 'chip_name': 'k10temp', 'label': 'Tctl'},
            ],
        }
        raw = cluster.normalize(results)
        self.assertEqual(len(raw['nodes']), 1)
        node = raw['nodes'][0]
        self.assertEqual(node['name'], 'pve')
        self.assertEqual(node['cpu_ratio'], 0.25)
        self.assertEqual(node['cores'], 16)
        self.assertEqual(node['load'], {'avg1': 0.5, 'avg5': 0.4, 'avg15': 0.3})
        self.assertEqual(node['memory_used'], 4 * 1024 ** 3)
        self.assertEqual(node['memory_ratio'], 0.25)
        self.assertEqual(node['swap_ratio'], 0.25)
        self.assertEqual(node['uptime'], 3600 * 30)
        self.assertEqual(node['temperature_c'], 45.5)
        self.assertEqual(raw['guests'], [])
        self.assertEqual(raw['storages'], [])

    def test_temperature_falls_back_to_coretemp(self):
        results = {'F': [
            {'host': 'pve2', 'temp_celsius': 61.0, 'chip_name': 'coretemp'},
            {'host': 'pve2', 'temp_celsius': 70.0, 'chip_name': 'iwlwifi'},
        ]}
        raw = cluster.normalize(results)
        self.assertEqual(raw['nodes'][0]['temperature_c'], 61.0)

    def test_guests_group_by_type_and_vmid(self):
        results = {'D': [
            {'object': 'qemu', 'vmid': 100, 'nodename': 'pve', 'host': 'web',
             'name': 'web', 'status': 'running', 'cpu': 0.05, 'cpus': 4},
            {'object': 'qemu', 'vmid': 100, 'nodename': 'pve', 'host': 'web',
             'mem': 1024 ** 3, 'maxmem': 4 * 1024 ** 3, 'uptime': 3600},
            {'object': 'lxc', 'vmid': 101, 'nodename': 'pve', 'host': 'db',
             'status': 'stopped', 'cpu': 0.0, 'cpus': 2},
        ]}
        raw = cluster.normalize(results)
        self.assertEqual([item['vmid'] for item in raw['guests']], [100, 101])
        web = raw['guests'][0]
        self.assertEqual(web['name'], 'web')
        self.assertEqual(web['node'], 'pve')
        self.assertEqual(web['type'], 'qemu')
        self.assertEqual(web['status'], 'running')
        self.assertEqual(web['cpu_ratio'], 0.05)
        self.assertEqual(web['cores'], 4)
        self.assertEqual(web['mem_used'], 1024 ** 3)
        self.assertEqual(web['mem_total'], 4 * 1024 ** 3)
        self.assertEqual(web['uptime'], 3600)
        self.assertEqual(raw['guests'][1]['status'], 'stopped')

    def test_guest_name_falls_back_to_host_label(self):
        results = {'D': [
            {'object': 'lxc', 'vmid': 102, 'nodename': 'pve', 'host': 'backup'},
        ]}
        raw = cluster.normalize(results)
        self.assertEqual(raw['guests'][0]['name'], 'backup')

    def test_storages_group_by_node_and_name(self):
        results = {'E': [
            {'host': 'local', 'nodename': 'pve', 'type': 'dir', 'used': 100,
             'total': 200, 'avail': 100, 'content': 'rootdir', 'active': 1},
            {'host': 'local', 'nodename': 'pve', 'active': 0},
            {'host': 'tank', 'nodename': 'pve2', 'type': 'zfspool', 'used': 1,
             'total': 4, 'content': 'images', 'active': 1},
        ]}
        raw = cluster.normalize(results)
        self.assertEqual(
            [(item['node'], item['name']) for item in raw['storages']],
            [('pve', 'local'), ('pve2', 'tank')],
        )
        local = raw['storages'][0]
        self.assertEqual(local['type'], 'dir')
        self.assertEqual(local['content'], 'rootdir')
        self.assertEqual(local['ratio'], 0.5)
        self.assertEqual(local['active'], False)
        self.assertEqual(raw['storages'][1]['ratio'], 0.25)

    def test_node_filter_applies_to_all_sections(self):
        results = {
            'A': [{'host': 'pve', 'cpu': 0.1}, {'host': 'pve2', 'cpu': 0.2}],
            'D': [
                {'object': 'qemu', 'vmid': 1, 'nodename': 'pve', 'host': 'a'},
                {'object': 'qemu', 'vmid': 2, 'nodename': 'pve2', 'host': 'b'},
            ],
            'E': [
                {'host': 'local', 'nodename': 'pve', 'used': 1, 'total': 2},
                {'host': 'local', 'nodename': 'pve2', 'used': 1, 'total': 2},
            ],
        }
        raw = cluster.normalize(results, node_filter=['pve2'])
        self.assertEqual([item['name'] for item in raw['nodes']], ['pve2'])
        self.assertEqual([item['vmid'] for item in raw['guests']], [2])
        self.assertEqual([item['node'] for item in raw['storages']], ['pve2'])

        raw = cluster.normalize(results, node_filter='pve2')
        self.assertEqual([item['name'] for item in raw['nodes']], ['pve2'])
        self.assertEqual([item['vmid'] for item in raw['guests']], [2])

    def test_missing_and_non_finite_values_stay_none(self):
        results = {
            'A': [{'host': 'pve', 'cpu': float('nan'), 'cpus': float('inf')}],
            'B': [{'host': 'pve', 'memused': 1024}, {'host': 'pve', 'memtotal': 0}],
            'D': [{'object': 'qemu', 'vmid': 1, 'nodename': 'pve', 'host': 'a',
                   'mem': float('nan'), 'maxmem': 1024}],
            'E': [{'host': 'local', 'nodename': 'pve', 'used': float('inf')}],
        }
        raw = cluster.normalize(results)
        node = raw['nodes'][0]
        self.assertIsNone(node['cpu_ratio'])
        self.assertIsNone(node['cores'])
        self.assertIsNone(node['memory_ratio'])
        self.assertIsNone(node['swap_ratio'])
        self.assertIsNone(node['uptime'])
        self.assertIsNone(node['temperature_c'])
        self.assertIsNone(raw['guests'][0]['mem_used'])
        self.assertIsNone(raw['guests'][0]['status'])
        self.assertIsNone(raw['storages'][0]['used'])
        self.assertIsNone(raw['storages'][0]['ratio'])

    def test_unknown_guest_objects_are_ignored(self):
        results = {'D': [
            {'object': 'node', 'vmid': 1, 'nodename': 'pve'},
            {'object': 'qemu', 'nodename': 'pve'},
        ]}
        self.assertEqual(cluster.normalize(results)['guests'], [])


class BuildViewTest(unittest.TestCase):
    def raw_view(self):
        return {
            'nodes': [
                {
                    'name': 'pve2', 'cpu_ratio': 0.95, 'cores': 8,
                    'load': {'avg1': 1.0, 'avg5': 0.5, 'avg15': None},
                    'memory_used': 8 * 1024 ** 3, 'memory_total': 16 * 1024 ** 3,
                    'memory_ratio': 0.5, 'swap_used': None, 'swap_total': None,
                    'swap_ratio': None, 'uptime': 90061, 'temperature_c': None,
                },
                {
                    'name': 'pve', 'cpu_ratio': 0.75, 'cores': 16,
                    'load': {'avg1': 0.5, 'avg5': 0.4, 'avg15': 0.3},
                    'memory_used': 1024 ** 3, 'memory_total': 2 * 1024 ** 3,
                    'memory_ratio': 0.5, 'swap_used': 1024 ** 3,
                    'swap_total': 4 * 1024 ** 3, 'swap_ratio': 0.25,
                    'uptime': 3600 * 30, 'temperature_c': 45.5,
                },
            ],
            'guests': [
                {'vmid': 101, 'name': 'db', 'node': 'pve', 'type': 'lxc',
                 'status': 'stopped', 'cpu_ratio': 0.0, 'cores': 2,
                 'mem_used': None, 'mem_total': None, 'uptime': None},
                {'vmid': 100, 'name': 'web', 'node': 'pve', 'type': 'qemu',
                 'status': 'running', 'cpu_ratio': 0.05, 'cores': 4,
                 'mem_used': 512 * 1024 ** 2, 'mem_total': 1024 ** 3,
                 'uptime': 7200},
                {'vmid': 50, 'name': 'cache', 'node': 'pve2', 'type': 'lxc',
                 'status': 'paused', 'cpu_ratio': 0.01, 'cores': 1,
                 'mem_used': 1, 'mem_total': 2, 'uptime': 60},
                {'vmid': 7, 'name': 'other', 'node': 'pve', 'type': 'qemu',
                 'status': 'migrating', 'cpu_ratio': None, 'cores': None,
                 'mem_used': None, 'mem_total': None, 'uptime': None},
            ],
            'storages': [
                {'name': 'tank', 'node': 'pve2', 'type': 'zfspool', 'content': 'images',
                 'used': 1, 'total': 4, 'avail': 3, 'ratio': 0.25, 'active': True},
                {'name': 'local', 'node': 'pve', 'type': 'dir', 'content': 'rootdir',
                 'used': 100 * 1024 ** 3, 'total': 200 * 1024 ** 3,
                 'avail': 100 * 1024 ** 3, 'ratio': 0.5, 'active': False},
            ],
        }

    def test_view_has_exact_shape_and_sorted_sections(self):
        view = cluster.build_view(self.raw_view(), now=1700000000)
        self.assertEqual(
            set(view), {'title', 'generated_at', 'source', 'summary', 'nodes', 'guests', 'storages'}
        )
        self.assertEqual(view['title'], 'Proxmox VE')
        self.assertEqual(view['source'], 'Grafana')
        self.assertEqual(view['summary'], {
            'nodes': 2, 'guests': 4, 'running': 1, 'vms': 2, 'lxcs': 2, 'storages': 2,
        })
        self.assertEqual([item['name'] for item in view['nodes']], ['pve', 'pve2'])
        self.assertEqual(
            [(item['node'], item['vmid']) for item in view['guests']],
            [('pve', 7), ('pve', 100), ('pve', 101), ('pve2', 50)],
        )
        self.assertEqual([item['name'] for item in view['storages']], ['local', 'tank'])

    def test_every_item_exposes_the_renderer_keys(self):
        view = cluster.build_view(self.raw_view(), now=1700000000)
        self.assertEqual(set(view['nodes'][0]), NODE_KEYS)
        self.assertEqual(set(view['guests'][0]), GUEST_KEYS)
        self.assertEqual(set(view['storages'][0]), STORAGE_KEYS)

    def test_node_formatting_and_levels(self):
        view = cluster.build_view(self.raw_view(), now=1700000000)
        pve = view['nodes'][0]
        self.assertEqual(pve['cpu_percent'], 75.0)
        self.assertEqual(pve['cpu_text'], '75.0%')
        self.assertEqual(pve['cpu_level'], 'warn')
        self.assertEqual(pve['cpu_note'], '45.5℃')
        self.assertEqual(pve['cores'], 16)
        self.assertEqual(pve['load'], '0.50 / 0.40 / 0.30')
        self.assertEqual(pve['memory_percent'], 50.0)
        self.assertEqual(pve['memory_text'], '1.00 GB / 2.00 GB')
        self.assertEqual(pve['memory_level'], 'good')
        self.assertEqual(pve['swap_percent'], 25.0)
        self.assertEqual(pve['swap_text'], '1.00 GB / 4.00 GB')
        self.assertEqual(pve['uptime'], '1 天 6 小时')
        self.assertEqual(pve['temperature_text'], '45.5℃')
        self.assertEqual(pve['status'], '正常')
        self.assertEqual(pve['status_level'], 'good')

        pve2 = view['nodes'][1]
        self.assertEqual(pve2['cpu_level'], 'bad')
        self.assertEqual(pve2['load'], '1.00 / 0.50 / --')
        self.assertEqual(pve2['temperature_text'], '--')
        self.assertEqual(pve2['cpu_note'], '')

    def test_missing_node_values_never_become_zero(self):
        view = cluster.build_view({
            'nodes': [{'name': 'pve'}],
            'guests': [],
            'storages': [],
        }, now=1700000000)
        node = view['nodes'][0]
        self.assertIsNone(node['cpu_percent'])
        self.assertEqual(node['cpu_text'], '--')
        self.assertIsNone(node['memory_percent'])
        self.assertEqual(node['memory_text'], '--')
        self.assertEqual(node['swap_text'], '--')
        self.assertEqual(node['load'], '--')
        self.assertEqual(node['uptime'], '--')
        self.assertEqual(node['status'], '部分数据缺失')
        self.assertEqual(node['status_level'], 'warn')
        self.assertEqual(node['temperature_text'], '--')

    def test_guest_labels_and_memory(self):
        view = cluster.build_view(self.raw_view(), now=1700000000)
        by_vmid = {item['vmid']: item for item in view['guests']}
        web = by_vmid[100]
        self.assertEqual(web['type_label'], 'VM')
        self.assertEqual(web['status_label'], '运行中')
        self.assertEqual(web['status_level'], 'good')
        self.assertEqual(web['cpu_percent'], 5.0)
        self.assertEqual(web['cpu_text'], '5.0%')
        self.assertEqual(web['mem_text'], '512.00 MB / 1.00 GB')
        self.assertEqual(web['mem_percent'], 50.0)
        self.assertEqual(web['uptime'], '2 小时 0 分')
        self.assertEqual(by_vmid[101]['type_label'], 'LXC')
        self.assertEqual(by_vmid[101]['status_label'], '已停止')
        self.assertEqual(by_vmid[101]['status_level'], 'warn')
        self.assertEqual(by_vmid[101]['mem_text'], '--')
        self.assertEqual(by_vmid[50]['status_label'], '已暂停')
        self.assertEqual(by_vmid[7]['status_label'], 'migrating')
        self.assertEqual(by_vmid[7]['status_level'], 'warn')
        self.assertEqual(by_vmid[7]['name'], 'other')

    def test_storage_formatting(self):
        view = cluster.build_view(self.raw_view(), now=1700000000)
        local = view['storages'][0]
        self.assertEqual(local['used_text'], '100.00 GB')
        self.assertEqual(local['total_text'], '200.00 GB')
        self.assertEqual(local['percent'], 50.0)
        self.assertEqual(local['percent_text'], '50.0%')
        self.assertEqual(local['level'], 'good')
        self.assertEqual(local['active'], False)
        self.assertEqual(local['type'], 'dir')
        self.assertEqual(local['content'], 'rootdir')
        self.assertEqual(view['storages'][1]['active'], True)

    def test_empty_sections_survive(self):
        view = cluster.build_view({}, now=1700000000)
        self.assertEqual(view['nodes'], [])
        self.assertEqual(view['guests'], [])
        self.assertEqual(view['storages'], [])
        self.assertEqual(view['summary']['guests'], 0)


class FetchTest(unittest.TestCase):
    def setUp(self):
        cluster.clear_cache()

    def results(self):
        return {
            'A': [{'host': 'pve', 'cpu': 0.25, 'cpus': 16}],
            'B': [
                {'host': 'pve', 'memused': 4 * 1024 ** 3, 'memtotal': 16 * 1024 ** 3},
                {'host': 'pve', 'swapused': 0, 'swaptotal': 4 * 1024 ** 3},
            ],
            'C': [{'host': 'pve', 'uptime': 86400}],
            'D': [{'object': 'qemu', 'vmid': 100, 'nodename': 'pve', 'host': 'web',
                   'name': 'web', 'status': 'running', 'cpu': 0.1, 'cpus': 4,
                   'mem': 1024 ** 3, 'maxmem': 2 * 1024 ** 3, 'uptime': 3600}],
            'E': [{'host': 'local', 'nodename': 'pve', 'type': 'dir', 'used': 50,
                   'total': 100, 'avail': 50, 'content': 'rootdir', 'active': 1}],
        }

    def test_fetch_builds_view_and_caches(self):
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value=self.results()) as query:
            first = cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='glsa_test', now=1000)
            second = cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='glsa_test', now=1010)
            cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='glsa_test', now=1100)
        self.assertEqual(first, second)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(first['summary']['nodes'], 1)
        self.assertEqual(first['summary']['guests'], 1)
        self.assertEqual(first['summary']['storages'], 1)
        self.assertEqual(first['nodes'][0]['cpu_text'], '25.0%')
        self.assertEqual(first['guests'][0]['name'], 'web')
        self.assertEqual(first['storages'][0]['percent_text'], '50.0%')

    def test_fetch_uses_cache_switch_to_bypass(self):
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value=self.results()) as query:
            cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='x', now=1000)
            cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='x', now=1000,
                          use_cache=False)
        self.assertEqual(query.call_count, 2)

    def test_fetch_override_datasource_uid_without_mutating_input(self):
        grafana_config = dict(GRAFANA_CONFIG)
        cluster_config = {**CLUSTER_CONFIG, 'datasource_uid': 'override-uid',
                          'window_minutes': 0, 'node_filter': 'pve, pve2'}
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value=self.results()) as query:
            cluster.fetch(cluster_config, grafana_config, token='x', now=1000)
        passed_config, queries = query.call_args.args[0], query.call_args.args[1]
        self.assertEqual(passed_config['datasource_uid'], 'override-uid')
        self.assertIn('range(start: -1m)', queries['A'])
        self.assertEqual(grafana_config['datasource_uid'], 'influx-uid')
        self.assertEqual(query.call_args.kwargs['token'], 'x')

    def test_fetch_raises_when_nothing_returned(self):
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value={'A': [], 'B': [], 'C': [], 'D': [], 'E': [], 'F': []}):
            with self.assertRaises(grafana.GrafanaError):
                cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='x', now=1000)
        self.assertIn('没有返回', str(grafana.last_error()))

    def test_fetch_only_temperature_data_still_counts_as_data(self):
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value={'F': [{'host': 'pve', 'temp_celsius': 40.0}]}):
            view = cluster.fetch(CLUSTER_CONFIG, GRAFANA_CONFIG, token='x', now=1000)
        self.assertEqual(view['nodes'][0]['temperature_text'], '40.0℃')

    def test_custom_title_and_temperature_toggle(self):
        cluster_config = {**CLUSTER_CONFIG, 'title': '生产集群', 'show_temperature': False,
                          'cache_seconds': 0}
        with patch.object(cluster.status_grafana, 'query_flux_batch',
                          return_value=self.results()) as query:
            view = cluster.fetch(cluster_config, GRAFANA_CONFIG, token='x', now=1000)
        self.assertEqual(view['title'], '生产集群')
        self.assertNotIn('F', query.call_args.args[1])


class ConfigTest(unittest.TestCase):
    def test_cluster_defaults(self):
        self.assertEqual(config_mod.DEFAULT_CONFIG['cluster'], {
            'enabled': False,
            'title': 'Proxmox VE',
            'bucket': 'proxmox',
            'datasource_uid': '',
            'window_minutes': 10,
            'node_filter': [],
            'show_temperature': True,
            'cache_seconds': 15,
        })

    def test_cluster_whitelist(self):
        self.assertEqual(config_mod.EDITABLE['cluster'], (
            'enabled', 'title', 'bucket', 'datasource_uid', 'window_minutes',
            'node_filter', 'show_temperature',
        ))

    def test_public_view_normalizes_node_filter(self):
        merged = config_mod.merge(config_mod.DEFAULT_CONFIG, {
            'cluster': {'node_filter': 'pve, pve2\npve3  pve4'},
        })
        cluster_view = config_mod.public_view(merged)['cluster']
        self.assertEqual(cluster_view['node_filter'], ['pve', 'pve2', 'pve3', 'pve4'])
        listed = config_mod.merge(config_mod.DEFAULT_CONFIG, {
            'cluster': {'node_filter': ['pve', ' pve2 ']},
        })
        self.assertEqual(config_mod.public_view(listed)['cluster']['node_filter'],
                         ['pve', 'pve2'])

    def test_apply_patch_accepts_cluster_fields_and_ignores_others(self):
        patched = config_mod.apply_patch(config_mod.merge(config_mod.DEFAULT_CONFIG, {}), {
            'cluster': {
                'enabled': True,
                'bucket': 'pve_prod',
                'node_filter': 'a,b',
                'cache_seconds': 999,
                'unknown': 'x',
            },
        })
        self.assertTrue(patched['cluster']['enabled'])
        self.assertEqual(patched['cluster']['bucket'], 'pve_prod')
        self.assertEqual(patched['cluster']['node_filter'], 'a,b')
        self.assertEqual(patched['cluster']['cache_seconds'], 15)
        self.assertNotIn('unknown', patched['cluster'])

    def test_merge_keeps_old_config_files_working(self):
        merged = config_mod.merge(config_mod.DEFAULT_CONFIG, {'grafana': {'url': 'x'}})
        self.assertEqual(merged['cluster']['bucket'], 'proxmox')
        self.assertEqual(merged['cluster']['node_filter'], [])


class SummaryTextTest(unittest.TestCase):
    def test_summary_mentions_nodes_guests_and_top_storages(self):
        view = cluster.build_view({
            'nodes': [{'name': 'pve', 'cpu_ratio': 0.5, 'memory_used': 1024 ** 3,
                       'memory_total': 2 * 1024 ** 3, 'memory_ratio': 0.5,
                       'uptime': 3600}],
            'guests': [
                {'vmid': 100, 'name': 'web', 'node': 'pve', 'type': 'qemu',
                 'status': 'running'},
                {'vmid': 101, 'name': 'db', 'node': 'pve', 'type': 'lxc',
                 'status': 'stopped'},
            ],
            'storages': [
                {'name': 'local', 'node': 'pve', 'used': 90, 'total': 100,
                 'ratio': 0.9, 'active': True},
                {'name': 'cold', 'node': 'pve', 'used': 1, 'total': 100,
                 'ratio': 0.01, 'active': True},
            ],
        }, now=1700000000)
        text = cluster.summary_text(view)
        self.assertIn('Proxmox VE', text)
        self.assertIn('节点 1', text)
        self.assertIn('访客 2', text)
        self.assertIn('运行 1', text)
        self.assertIn('pve：CPU 50.0%', text)
        self.assertIn('1.00 GB / 2.00 GB', text)
        self.assertIn('VM 1', text)
        self.assertIn('LXC 1', text)
        self.assertIn('local 90.0%', text)
        self.assertIn('cold 1.0%', text)

    def test_summary_survives_empty_view(self):
        text = cluster.summary_text({})
        self.assertIn('节点 0', text)


if __name__ == '__main__':
    unittest.main()
