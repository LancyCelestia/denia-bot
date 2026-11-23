"""The vector guard must reject non-SiliconFlow services before any write."""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'nonebot'))
import rag_client


def _health(**overrides):
    data = {
        'ready': True,
        'embed_backend': 'siliconflow',
        'model': 'BAAI/bge-m3',
        'dim': 1024,
        'chunks': 42,
        'index': '/tmp/index-siliconflow',
    }
    data.update(overrides)
    return data


def _vector():
    return [1.0] + [0.0] * 1023


def _run(handler, text='hello'):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rag_client.embed(text, client=client)
    return asyncio.run(run())


def test_health_problem_accepts_cloud_backend():
    assert rag_client.health_problem(_health()) == ''


@pytest.mark.parametrize('overrides, fragment', [
    ({'ready': False}, '未就绪'),
    ({'embed_backend': 'cpu'}, '后端不一致'),
    ({'model': 'bge-m3-fp16-v1'}, '模型不一致'),
    ({'dim': 768}, '维度不一致'),
    ({'dim': 'abc'}, '维度无效'),
])
def test_health_problem_reports_mismatch(overrides, fragment):
    assert fragment in rag_client.health_problem(_health(**overrides))


def test_valid_service_returns_vector_and_checks_model():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/health':
            return httpx.Response(200, json=_health())
        return httpx.Response(200, json={'model': 'siliconflow-bge-m3-v1', 'vector': _vector()})

    assert _run(handler) == _vector()
    assert paths == ['/health', '/embed']


def test_wrong_backend_is_rejected_before_embed():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=_health(embed_backend='cpu'))

    with pytest.raises(rag_client.RagConsistencyError, match='后端不一致'):
        _run(handler)
    assert paths == ['/health']


def test_unready_service_is_rejected_before_embed():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=_health(ready=False))

    with pytest.raises(rag_client.RagConsistencyError, match='未就绪'):
        _run(handler)
    assert paths == ['/health']


@pytest.mark.parametrize('payload', [[], {}, {'model': 'wrong', 'vector': _vector()},
                                     {'model': 'siliconflow-bge-m3-v1', 'vector': [1.0]},
                                     {'model': 'siliconflow-bge-m3-v1'}])
def test_malformed_embed_json_is_rejected(payload):
    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json=_health())
        return httpx.Response(200, json=payload)

    with pytest.raises(rag_client.RagConsistencyError, match='无效结果'):
        _run(handler)


def test_provider_error_is_reported():
    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json=_health())
        return httpx.Response(503, text='provider overloaded')

    with pytest.raises(rag_client.RagEmbeddingError, match='503') as caught:
        _run(handler)
    assert 'provider overloaded' in str(caught.value)


def test_offline_service_does_not_fall_back():
    def handler(request):
        raise httpx.ConnectError('offline', request=request)

    with pytest.raises(rag_client.RagEmbeddingError, match='不可用'):
        _run(handler)


def test_meme_learning_embed_delegates_to_guard():
    import meme_learning

    async def fake(text):
        assert text == '描述'
        return _vector()

    original = meme_learning._rag_embed
    meme_learning._rag_embed = fake
    try:
        assert asyncio.run(meme_learning.embed('描述')) == _vector()
    finally:
        meme_learning._rag_embed = original
