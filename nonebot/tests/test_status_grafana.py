# 从 Grafana 读取 CPU 温度：解析、缓存与降级用例。
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_grafana as grafana


CONFIG = {
    'enabled': True,
    'url': 'http://grafana.local:3000',
    'datasource_uid': 'prom-uid',
    'temp_query': 'max(node_hwmon_temp_celsius)',
    'avg_window_minutes': 60,
    'timeout_seconds': 3,
    'cache_seconds': 60,
    'query_mode': 'prometheus',
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


def matrix(*points):
    return {
        'status': 'success',
        'data': {
            'resultType': 'matrix',
            'result': [{'metric': {}, 'values': [[ts, str(value)] for ts, value in points]}],
        },
    }


class GrafanaTempTest(unittest.TestCase):
    def setUp(self):
        grafana.CACHE.clear()

    def test_current_is_last_sample_and_average_is_window_mean(self):
        response = FakeResponse(matrix((100, 40.0), (160, 42.0), (220, 44.0)))
        with patch.object(grafana._OPENER, 'open', return_value=response) as urlopen, \
                tempfile.TemporaryDirectory() as tmp:
            result = grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1000)
        self.assertEqual(result, {
            'current_c': 44.0, 'avg_c': 42.0, 'window_minutes': 60, 'samples': 3,
        })
        request = urlopen.call_args.args[0]
        self.assertIn('/api/datasources/proxy/uid/prom-uid/api/v1/query_range', request.full_url)
        self.assertIn('max%28node_hwmon_temp_celsius%29', request.full_url)
        self.assertEqual(request.get_header('Authorization'), 'Bearer glsa_test')

    def test_disabled_returns_none_without_network(self):
        with patch.object(grafana._OPENER, 'open') as urlopen:
            self.assertIsNone(grafana.fetch_temp({**CONFIG, 'enabled': False}, token='x'))
        urlopen.assert_not_called()

    def test_missing_token_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp(CONFIG, root=tmp))
        self.assertIn('API Key', grafana.last_error())

    def test_invalid_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp({**CONFIG, 'url': 'file:///etc/passwd'}, token='x', root=tmp))
        self.assertIn('http', grafana.last_error())

    def test_cache_avoids_second_request(self):
        response = FakeResponse(matrix((100, 40.0), (160, 42.0)))
        with patch.object(grafana._OPENER, 'open', return_value=response) as urlopen, \
                tempfile.TemporaryDirectory() as tmp:
            first = grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1000)
            second = grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1030)
        self.assertEqual(first, second)
        self.assertEqual(urlopen.call_count, 1)

    def test_empty_result_returns_none(self):
        response = FakeResponse({'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})
        with patch.object(grafana._OPENER, 'open', return_value=response), \
                tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp(CONFIG, token='x', root=tmp, now=1000))

    def test_secret_roundtrip_and_panel_view_masks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(grafana.load_secret(tmp), '')
            grafana.save_secret('glsa_secret', tmp)
            self.assertEqual(grafana.load_secret(tmp), 'glsa_secret')
            view = grafana.panel_view(CONFIG, root=tmp)
            self.assertTrue(view['configured'])
            self.assertNotIn('glsa_secret', json.dumps(view))
            grafana.clear_secret(tmp)
            self.assertFalse(grafana.panel_view(CONFIG, root=tmp)['configured'])

    def test_non_json_response_reports_status_and_snippet(self):
        response = FakeResponse(b'<html>Not Found</html>', content_type='text/html', status=404)
        with patch.object(grafana._OPENER, 'open', return_value=response), \
                tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1000))
        error = grafana.last_error()
        self.assertIn('非 JSON', error)
        self.assertIn('404', error)
        self.assertIn('text/html', error)

    def test_http_error_body_is_surfaced(self):
        import io
        import urllib.error
        failure = urllib.error.HTTPError(
            'http://grafana.local', 401, 'Unauthorized', {},
            io.BytesIO(b'{"message":"Invalid API key"}'),
        )
        with patch.object(grafana._OPENER, 'open', side_effect=failure), \
                tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1000))
        self.assertIn('401', grafana.last_error())
        self.assertIn('Invalid API key', grafana.last_error())

    def test_datasource_list_parses_uid_and_type(self):
        payload = [
            {'uid': 'P-UID', 'name': 'Prometheus', 'type': 'prometheus', 'isDefault': True},
            {'uid': 'L-UID', 'name': 'Loki', 'type': 'loki', 'isDefault': False},
        ]
        with patch.object(grafana._OPENER, 'open', return_value=FakeResponse(payload)), \
                tempfile.TemporaryDirectory() as tmp:
            items = grafana.fetch_datasources({'url': 'http://grafana.local:3000'}, token='glsa_test', root=tmp)
        self.assertEqual(items, [
            {'uid': 'P-UID', 'name': 'Prometheus', 'type': 'prometheus', 'default': True},
            {'uid': 'L-UID', 'name': 'Loki', 'type': 'loki', 'default': False},
        ])

    def test_base_url_drops_query_and_page_paths(self):
        self.assertEqual(grafana._base_url({'url': 'http://host:3000/?orgId=1'}), 'http://host:3000')
        self.assertEqual(grafana._base_url({'url': 'http://host:3000/d/abc?orgId=1'}), 'http://host:3000')
        self.assertEqual(
            grafana._base_url({'url': 'http://host:3000/grafana/d/abc'}), 'http://host:3000/grafana',
        )
        with self.assertRaises(ValueError):
            grafana._base_url({'url': 'host:3000'})

    def test_flux_query_parses_frames(self):
        payload = {'results': {'A': {'status': 200, 'frames': [{
            'schema': {'fields': [{'name': '_time', 'type': 'time'}, {'name': '_value', 'type': 'number'}]},
            'data': {'values': [[100, 160, 220], [40.0, 42.0, 44.0]]},
        }]}}}
        config = {**CONFIG, 'query_mode': 'flux', 'temp_query': 'from(bucket: "proxmox") |> range(start: -{window}m)'}
        with patch.object(grafana._OPENER, 'open', return_value=FakeResponse(payload)) as opener, \
                tempfile.TemporaryDirectory() as tmp:
            result = grafana.fetch_temp(config, token='glsa_test', root=tmp, now=1000)
        self.assertEqual(result, {
            'current_c': 44.0, 'avg_c': 42.0, 'window_minutes': 60, 'samples': 3,
        })
        request = opener.call_args.args[0]
        self.assertIn('/api/ds/query', request.full_url)
        body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(body['queries'][0]['datasource']['type'], 'influxdb')
        self.assertIn('-60m', body['queries'][0]['query'])

    def test_auto_detects_influxdb_flux(self):
        datasource = {'type': 'influxdb', 'jsonData': {'version': 'Flux'}}
        payload = {'results': {'A': {'status': 200, 'frames': [{
            'schema': {'fields': [{'name': '_time', 'type': 'time'}, {'name': '_value', 'type': 'number'}]},
            'data': {'values': [[100, 160], [40.0, 41.0]]},
        }]}}}
        config = {**CONFIG, 'query_mode': 'auto'}
        with patch.object(
                grafana._OPENER, 'open',
                side_effect=[FakeResponse(datasource), FakeResponse(payload)],
        ) as opener, tempfile.TemporaryDirectory() as tmp:
            result = grafana.fetch_temp(config, token='glsa_test', root=tmp, now=1000)
        self.assertEqual(result['current_c'], 41.0)
        self.assertEqual(opener.call_count, 2)

    def test_influxql_is_rejected(self):
        datasource = {'type': 'influxdb', 'jsonData': {'version': 'InfluxQL'}}
        with patch.object(grafana._OPENER, 'open', return_value=FakeResponse(datasource)), \
                tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp({**CONFIG, 'query_mode': 'auto'}, token='glsa_test', root=tmp, now=1000))
        self.assertIn('InfluxQL', grafana.last_error())

    def test_html_response_error_includes_request_url_and_hint(self):
        response = FakeResponse(b'<!doctype html><html><body>Grafana</body></html>', content_type='text/html')
        with patch.object(grafana._OPENER, 'open', return_value=response), \
                tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(grafana.fetch_temp(CONFIG, token='glsa_test', root=tmp, now=1000))
        error = grafana.last_error()
        self.assertIn('/api/datasources/proxy/uid/prom-uid/api/v1/query_range', error)
        self.assertIn('前端页面', error)


if __name__ == '__main__':
    unittest.main()
