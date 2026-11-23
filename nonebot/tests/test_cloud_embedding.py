import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rag'))
sys.path.insert(0, str(ROOT / 'nonebot'))
import siliconflow
import embedding_panel
import serve_cloud


def _key_file(tmp_path):
    path = tmp_path / 'key.json'
    path.write_text(json.dumps({'api_key': 'sk-test'}))
    return path


def _vectors(count):
    return {'data': [{'index': i, 'embedding': [2.0] + [0.0] * 1023} for i in reversed(range(count))]}


def test_cloud_client_batches_and_normalizes(tmp_path):
    key_file = _key_file(tmp_path)
    sizes = []

    def respond(request, timeout):
        payload = json.loads(request.data)
        sizes.append(len(payload['input']))
        return io.BytesIO(json.dumps(_vectors(sizes[-1])).encode())

    with patch.object(siliconflow, 'KEY_FILE', key_file), patch('urllib.request.urlopen', respond):
        result = siliconflow.SiliconFlowEmbedder().encode(['x'] * 17)
    assert sizes == [16, 1]
    assert result.shape == (17, 1024)
    assert np.allclose(np.linalg.norm(result, axis=1), 1)


def test_cloud_client_rejects_bad_vectors(tmp_path):
    key_file = _key_file(tmp_path)
    response = io.BytesIO(json.dumps({'data': [{'index': 0, 'embedding': [0.0]}]}).encode())
    with patch.object(siliconflow, 'KEY_FILE', key_file), patch('urllib.request.urlopen', return_value=response):
        with pytest.raises(siliconflow.CloudEmbeddingError, match='invalid embedding response'):
            siliconflow.SiliconFlowEmbedder().encode(['x'])


def test_cloud_client_uses_configurable_timeout(tmp_path):
    key_file = _key_file(tmp_path)
    seen = []

    def respond(request, timeout):
        seen.append(timeout)
        return io.BytesIO(json.dumps(_vectors(1)).encode())

    with patch.object(siliconflow, 'KEY_FILE', key_file), \
            patch.dict(os.environ, {'KB_EMBED_TIMEOUT': '7.5'}), \
            patch('urllib.request.urlopen', respond):
        siliconflow.SiliconFlowEmbedder().encode(['x'])
    assert seen == [7.5]


def test_cloud_client_retries_transient_failures(tmp_path):
    key_file = _key_file(tmp_path)
    calls = []
    errors = [
        urllib.error.HTTPError('https://api.siliconflow.cn/v1/embeddings', 503, 'busy', {}, None),
        urllib.error.HTTPError('https://api.siliconflow.cn/v1/embeddings', 429, 'slow down', {}, None),
    ]

    def flaky(request, timeout):
        calls.append(timeout)
        if errors:
            raise errors.pop(0)
        return io.BytesIO(json.dumps(_vectors(1)).encode())

    with patch.object(siliconflow, 'KEY_FILE', key_file), \
            patch.dict(os.environ, {'KB_EMBED_RETRIES': '2'}), \
            patch('siliconflow.time.sleep') as sleep, \
            patch('urllib.request.urlopen', flaky):
        result = siliconflow.SiliconFlowEmbedder().encode(['x'])
    assert result.shape == (1, 1024)
    assert len(calls) == 3
    assert sleep.call_count == 2


def test_cloud_client_does_not_retry_permanent_http_errors(tmp_path):
    key_file = _key_file(tmp_path)
    calls = []

    def bad_request(request, timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError('https://api.siliconflow.cn/v1/embeddings', 400, 'bad request', {}, None)

    with patch.object(siliconflow, 'KEY_FILE', key_file), \
            patch.dict(os.environ, {'KB_EMBED_RETRIES': '3'}), \
            patch('siliconflow.time.sleep') as sleep, \
            patch('urllib.request.urlopen', bad_request):
        with pytest.raises(siliconflow.CloudEmbeddingError, match='HTTP 400'):
            siliconflow.SiliconFlowEmbedder().encode(['x'])
    assert len(calls) == 1
    assert sleep.call_count == 0


def test_cloud_client_reports_exhausted_retries(tmp_path):
    key_file = _key_file(tmp_path)
    calls = []

    def unavailable(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError('network down')

    with patch.object(siliconflow, 'KEY_FILE', key_file), \
            patch.dict(os.environ, {'KB_EMBED_RETRIES': '1'}), \
            patch('siliconflow.time.sleep'), \
            patch('urllib.request.urlopen', unavailable):
        with pytest.raises(siliconflow.CloudEmbeddingError, match='network down'):
            siliconflow.SiliconFlowEmbedder().encode(['x'])
    assert len(calls) == 2


def test_dashboard_key_requires_auth_and_never_returns_secret(tmp_path):
    app = FastAPI()
    def authorized(req):
        if req.headers.get('authorization') != 'test':
            raise HTTPException(401)
    embedding_panel.register(app, authorized=authorized, mutate=authorized, root=tmp_path)
    client = TestClient(app)
    assert client.get('/api/embedding').status_code == 401
    assert client.post('/api/embedding', json={'api_key': 'sk-secret'}).status_code == 401
    assert client.post('/api/embedding', headers={'authorization': 'test'}, json={'api_key': 'sk-secret'}).status_code == 200
    response = client.get('/api/embedding', headers={'authorization': 'test'})
    assert response.json()['configured'] is True
    assert 'sk-secret' not in response.text


def test_cloud_embed_interface_uses_distinct_model_tag():
    class FakeEmbedder:
        def encode(self, texts):
            return np.ones((len(texts), 1024), dtype=np.float32)

    with patch.dict(serve_cloud.state, {'emb': FakeEmbedder()}):
        data = serve_cloud.embed(serve_cloud.EmbedReq(text='hello'))
    assert data['model'] == 'siliconflow-bge-m3-v1'
    assert len(data['vector']) == 1024
