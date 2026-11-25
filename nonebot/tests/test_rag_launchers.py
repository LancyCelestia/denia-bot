"""Wiring tests for the Windows launchers and the legacy-local boundary."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / 'tools'
PS_START = TOOLS / '启动所有.ps1'
BAT_START = TOOLS / '启动所有.bat'
PS_STOP = TOOLS / '停止所有.ps1'
BAT_STOP = TOOLS / '停止所有.bat'
REQUIREMENTS = REPO / 'requirements.lock'


def test_start_launchers_use_the_cloud_entrypoint():
    ps = PS_START.read_text(encoding='utf-8-sig')
    bat = BAT_START.read_text(encoding='utf-8')
    for text in (ps, bat):
        assert 'serve_cloud.py' in text
        assert 'index-siliconflow' in text
        assert 'KB_CLOUD_INDEX' in text
        assert 'embedding_secrets.json' in text
        assert 'rag\\index\\vectors.npy' not in text


def test_missing_rag_prerequisites_are_explicit():
    ps = PS_START.read_text(encoding='utf-8-sig')
    bat = BAT_START.read_text(encoding='utf-8')
    assert 'RAG 未启用（需要 SiliconFlow Key/索引）' in ps
    assert 'RAG not enabled (needs SiliconFlow key/index)' in bat
    assert ':rag_no_index' in bat
    assert ':rag_no_key' in bat


def test_stop_scripts_recognize_cloud_and_legacy_processes():
    ps = PS_STOP.read_text(encoding='utf-8-sig')
    bat = BAT_STOP.read_text(encoding='utf-8')
    assert 'serve(?:_cloud)?\\.py' in ps
    assert 'serve(?:_cloud)?\\.py' in bat


def test_start_and_stop_launchers_manage_the_dashboard():
    ps_start = PS_START.read_text(encoding='utf-8-sig')
    bat_start = BAT_START.read_text(encoding='utf-8')
    for text in (ps_start, bat_start):
        assert 'dashboard:app' in text
        assert '--port' in text

    ps_stop = PS_STOP.read_text(encoding='utf-8-sig')
    bat_stop = BAT_STOP.read_text(encoding='utf-8')
    for text in (ps_stop, bat_stop):
        assert 'dashboard(?::app|\\.py)' in text


def test_rag_tokenizer_dependency_is_locked():
    assert 'jieba==' in REQUIREMENTS.read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows batch launcher only')
def test_bat_launcher_parses_without_kb_cloud_index():
    env = os.environ.copy()
    env.pop('KB_CLOUD_INDEX', None)
    env['QQBOT_NO_PAUSE'] = '1'
    env['QQBOT_DRY_RUN'] = '1'
    result = subprocess.run(
        [env.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(BAT_START), '--no-pause'],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'launcher configuration parsed' in result.stdout


def test_legacy_onnx_entrypoints_live_under_legacy_local():
    for name in ('serve.py', 'build_index.py', 'to_fp16.py', 'download_model.sh',
                 'gpu_smoke.py', 'verify_gpu.py', 'analyze_gate.py', 'verify_vs_ckpt.py'):
        assert (REPO / 'rag' / 'legacy-local' / name).is_file(), name
        assert not (REPO / 'rag' / name).exists(), name
    readme = (REPO / 'rag' / 'legacy-local' / 'README.md').read_text(encoding='utf-8')
    assert '不再支持' in readme
    assert 'SiliconFlow' in readme


def test_supported_docs_only_point_at_the_cloud_path():
    for relative in ('README.md', 'docs/OPERATIONS.md', 'docs/PITFALLS.md'):
        text = (REPO / relative).read_text(encoding='utf-8')
        assert 'serve_cloud.py' in text, relative
        assert 'python serve.py' not in text, relative
        assert 'build_index.py --dir' not in text, relative
