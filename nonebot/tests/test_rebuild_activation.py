"""Windows/non-systemd index activation: swap, health wait, rollback."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rag'))
import rebuild_index
import service_control


def _repo(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'rag').mkdir(parents=True)
    (repo / 'rag' / 'serve_cloud.py').write_text('# entrypoint\n', encoding='utf-8')
    return repo


def _index(path, marker):
    path.mkdir(parents=True)
    (path / 'meta.json').write_text(marker, encoding='utf-8')
    return path


def test_process_activation_swaps_and_starts_cloud_service(tmp_path):
    repo = _repo(tmp_path)
    active = _index(repo / 'rag' / 'index-siliconflow', 'old')
    output = _index(repo / 'rag' / 'index-siliconflow.rebuild-1', 'new')

    with patch.object(service_control, 'stop_listener', return_value=[123]) as stop, \
            patch.object(service_control, 'start_cloud_service', return_value=object()) as start, \
            patch.object(rebuild_index, '_probe_health', return_value={'chunks': 10}), \
            patch.object(rebuild_index, '_wait_for_health', return_value={'ready': True}) as health:
        message = rebuild_index._activate_process(output, active, 'http://127.0.0.1:8091/health', 12,
                                                  repo=repo)

    assert message.startswith('activated index-siliconflow')
    assert (active / 'meta.json').read_text(encoding='utf-8') == 'new'
    previous = list((repo / 'rag').glob('index-siliconflow.previous-*'))
    assert len(previous) == 1
    assert (previous[0] / 'meta.json').read_text(encoding='utf-8') == 'old'
    stop.assert_called_once_with(8091)
    start.assert_called_once()
    assert start.call_args.kwargs['index'] == active
    assert start.call_args.kwargs['port'] == 8091
    assert health.call_args.kwargs['expected_index'] == active


def test_process_activation_rolls_back_and_restarts_old_service(tmp_path):
    repo = _repo(tmp_path)
    active = _index(repo / 'rag' / 'index-siliconflow', 'old')
    output = _index(repo / 'rag' / 'index-siliconflow.rebuild-1', 'new')
    seen = []

    def health(url, chunks, seconds=60, expected_index=None):
        seen.append((chunks, expected_index))
        if len(seen) == 1:
            raise RuntimeError('new index unhealthy')
        return {'ready': True}

    with patch.object(service_control, 'stop_listener', return_value=[123]), \
            patch.object(service_control, 'start_cloud_service', return_value=object()) as start, \
            patch.object(rebuild_index, '_probe_health', return_value={'chunks': 7}), \
            patch.object(rebuild_index, '_wait_for_health', side_effect=health):
        with pytest.raises(RuntimeError, match='new index unhealthy'):
            rebuild_index._activate_process(output, active, 'http://127.0.0.1:8091/health', 12,
                                            repo=repo)

    assert (active / 'meta.json').read_text(encoding='utf-8') == 'old'
    failed = list((repo / 'rag').glob('index-siliconflow.failed-*'))
    assert len(failed) == 1
    assert (failed[0] / 'meta.json').read_text(encoding='utf-8') == 'new'
    assert start.call_count == 2
    assert start.call_args.kwargs['index'] == active
    assert seen[-1][0] == 7
    assert seen[-1][1] == active


def test_activation_dispatch_uses_process_path_without_systemd():
    with patch.object(rebuild_index, '_systemd_available', return_value=False), \
            patch.object(rebuild_index, '_activate_process', return_value='activated') as process:
        assert rebuild_index.activate_index('out', 'active', '', 'http://127.0.0.1:8091/health', 3) == 'activated'
    process.assert_called_once()


def test_wait_for_health_requires_expected_index(tmp_path):
    active = _index(tmp_path / 'index-siliconflow', 'new')
    wrong = io.BytesIO(json.dumps({'ready': True, 'chunks': 3, 'index': str(tmp_path / 'other')}).encode())
    right = io.BytesIO(json.dumps({'ready': True, 'chunks': 3, 'index': str(active)}).encode())
    with patch('rebuild_index.urllib.request.urlopen', side_effect=[wrong, right]) as urlopen, \
            patch('rebuild_index.time.sleep'):
        data = rebuild_index._wait_for_health('http://127.0.0.1:8091/health', 3, seconds=5,
                                              expected_index=active)
    assert data['index'] == str(active)
    assert urlopen.call_count == 2


def test_start_cloud_service_sets_cwd_env_and_logs(tmp_path):
    repo = _repo(tmp_path)
    active = _index(repo / 'rag' / 'index-siliconflow', 'new')
    with patch.object(service_control, 'rag_python', return_value='python-x'), \
            patch.object(service_control.subprocess, 'Popen', return_value=object()) as popen:
        service_control.start_cloud_service(repo, port=8091, index=active, log_dir=repo / 'logs')

    args, kwargs = popen.call_args
    assert args[0] == ['python-x', str((repo / 'rag' / 'serve_cloud.py').resolve()), '--port', '8091']
    assert kwargs['cwd'] == str((repo / 'rag').resolve())
    assert kwargs['env']['KB_CLOUD_INDEX'] == str(active)
    assert kwargs['env']['KB_RAG_PORT'] == '8091'
    assert (repo / 'logs' / 'rag.out.log').exists()
    assert (repo / 'logs' / 'rag.err.log').exists()
