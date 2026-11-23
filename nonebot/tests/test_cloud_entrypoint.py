"""Tests for the cloud RAG entrypoint and its /health contract."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rag'))
sys.path.insert(0, str(ROOT / 'nonebot'))
import serve_cloud


def _index(tmp_path):
    path = tmp_path / 'index-siliconflow'
    path.mkdir()
    return path


class FakeIndex:
    def __init__(self):
        self.ready = True
        self.size = 42
        self.vectors = np.zeros((2, 4), dtype=np.float32)
        self.meta = {'dim': 1024, 'gate_stats': {'probe_max': 0.1}, 'sources': ['knowledge/a.md']}
        self.lexicon = {'达妮娅': [1]}
        self.gate_margin = 0.12


def test_health_reports_the_loaded_index(tmp_path, monkeypatch):
    index = _index(tmp_path)
    monkeypatch.setattr(serve_cloud.core, 'INDEX_DIR', index)
    monkeypatch.setitem(serve_cloud.state, 'idx', FakeIndex())
    monkeypatch.setattr(serve_cloud, 'key_configured', lambda: True)

    data = serve_cloud.health()

    assert data['ready'] is True
    assert data['chunks'] == 42
    assert data['embed_backend'] == 'siliconflow'
    assert data['model'] == 'BAAI/bge-m3'
    assert data['index'] == str(index.resolve())
    assert data['dim'] == 1024


def test_health_is_not_ready_without_key(tmp_path, monkeypatch):
    index = _index(tmp_path)
    monkeypatch.setattr(serve_cloud.core, 'INDEX_DIR', index)
    monkeypatch.setitem(serve_cloud.state, 'idx', FakeIndex())
    monkeypatch.setattr(serve_cloud, 'key_configured', lambda: False)

    assert serve_cloud.health()['ready'] is False


def test_main_starts_uvicorn_with_port_and_host():
    with patch('uvicorn.run') as run:
        assert serve_cloud.main(['--host', '127.0.0.1', '--port', '8123']) == 0
    run.assert_called_once()
    kwargs = run.call_args.kwargs
    assert kwargs['host'] == '127.0.0.1'
    assert kwargs['port'] == 8123
    assert kwargs['log_level'] == 'info'


def test_main_reads_port_from_environment(monkeypatch):
    monkeypatch.setenv('KB_RAG_PORT', '8134')
    with patch('uvicorn.run') as run:
        assert serve_cloud.main([]) == 0
    assert run.call_args.kwargs['port'] == 8134
