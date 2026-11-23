"""LLM 延迟直方图与 p95 的断言测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import status_llm
import status_store


class LLMQualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = status_store.StateStore(Path(self.tmp.name) / 'statuscard-state.json')
        self.quality = status_llm.LLMQuality(self.store, enabled=lambda: True)

    def tearDown(self):
        self.tmp.cleanup()

    def record_latencies(self, values, *, day: str = '2026-11-23'):
        for value in values:
            self.quality.record('test-model', ok=True, latency_ms=value, when=day)

    def test_p95_uses_the_successful_latency_histogram(self):
        self.record_latencies((100, 200, 300, 400, 500, 600, 700, 800, 900, 1000))
        summary = self.quality.summary(days=1)
        self.assertEqual(summary['ok'], 10)
        self.assertEqual(summary['p95_ms'], 1000.0)
        self.assertAlmostEqual(summary['avg_ms'], 550.0)

    def test_failures_do_not_enter_the_p95(self):
        self.quality.record('test-model', ok=False, latency_ms=60000)
        summary = self.quality.summary(days=1)
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['ok'], 0)
        self.assertIsNone(summary['p95_ms'])

    def test_legacy_bucket_without_histogram_is_unknown(self):
        day = '2026-11-23'
        self.store.update_section('llm', days={
            day: {
                'total': 1, 'ok': 1, 'fail': 0,
                'latency_ms': 120.0, 'ttft_ms': 0.0, 'ttft_n': 0,
                'by_model': {'old': {'total': 1, 'ok': 1, 'fail': 0}},
            },
        })
        self.store.save()
        self.assertIsNone(self.quality.summary(day=day)['p95_ms'])

    def test_histograms_merge_across_days(self):
        self.record_latencies((100, 100), day='2026-11-22')
        self.record_latencies((2000, 2000), day='2026-11-23')
        summary = self.quality.summary(days=7)
        self.assertEqual(summary['ok'], 4)
        self.assertEqual(summary['p95_ms'], 2000.0)


if __name__ == '__main__':
    unittest.main()
