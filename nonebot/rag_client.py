"""Guarded client for the local cloud RAG embedding service.

Every memory / meme-learning vector write goes through `embed()`.  It checks
`/health` first and refuses to use a service that is not the SiliconFlow
`BAAI/bge-m3` backend, so a local ONNX service cannot silently produce vectors
that get labelled `siliconflow-bge-m3-v1`.
"""

from __future__ import annotations

import os

import httpx

EXPECTED_BACKEND = 'siliconflow'
EXPECTED_MODEL = 'BAAI/bge-m3'
MODEL_ID = 'siliconflow-bge-m3-v1'
DIM = 1024
DEFAULT_URL = 'http://127.0.0.1:8091'
DEFAULT_TIMEOUT = 8.0


class RagEmbeddingError(RuntimeError):
    """The service is unavailable or returned an unusable vector."""


class RagConsistencyError(RagEmbeddingError):
    """The service is not the configured SiliconFlow backend; do not write."""


def base_url() -> str:
    return (os.environ.get('KB_RAG_URL') or DEFAULT_URL).rstrip('/')


def health_problem(health) -> str:
    if not isinstance(health, dict):
        return 'RAG /health 返回格式无效'
    if health.get('ready') is not True:
        return 'RAG 服务未就绪（/health ready=false）'
    backend = health.get('embed_backend')
    if backend != EXPECTED_BACKEND:
        return f'RAG 向量后端不一致：期望 {EXPECTED_BACKEND}，实际 {backend or "未报告"}'
    model = health.get('model') or health.get('embed_model')
    if model != EXPECTED_MODEL:
        return f'RAG 向量模型不一致：期望 {EXPECTED_MODEL}，实际 {model or "未报告"}'
    dim = health.get('dim')
    if dim is not None:
        try:
            dim = int(dim)
        except (TypeError, ValueError):
            return f'RAG 向量维度无效：{dim!r}'
        if dim != DIM:
            return f'RAG 向量维度不一致：期望 {DIM}，实际 {dim}'
    return ''


def validate_health(health) -> dict:
    problem = health_problem(health)
    if problem:
        raise RagConsistencyError(problem)
    return health


def _timeout() -> float:
    try:
        value = float(os.environ.get('KB_RAG_TIMEOUT', DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _http_detail(exc: httpx.HTTPError) -> str:
    response = getattr(exc, 'response', None)
    if response is None:
        return ''
    try:
        text = ' '.join(response.text.split())[:200]
    except Exception:  # noqa: BLE001 - diagnostics only
        return ''
    return f': {text}' if text else ''


async def embed(text: str, *, client: httpx.AsyncClient | None = None) -> list[float]:
    """Return a validated 1024-dim vector, or raise instead of falling back."""
    text = str(text or '')[:2000]
    if not text.strip():
        raise RagEmbeddingError('向量文本为空')
    base = base_url()
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=_timeout(), trust_env=False)
    try:
        try:
            response = await client.get(base + '/health')
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RagEmbeddingError(f'RAG 服务不可用：{exc}{_http_detail(exc)}') from exc
        try:
            validate_health(response.json())
        except ValueError as exc:
            raise RagEmbeddingError('RAG /health 返回的不是 JSON') from exc
        try:
            response = await client.post(base + '/embed', json={'text': text})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RagEmbeddingError(f'RAG 向量请求失败：{exc}{_http_detail(exc)}') from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise RagEmbeddingError('RAG /embed 返回的不是 JSON') from exc
        vector = data.get('vector') if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get('model') != MODEL_ID or not isinstance(vector, list) or len(vector) != DIM:
            raise RagConsistencyError('向量服务返回无效结果（模型或维度不匹配），拒绝写入')
        return [float(value) for value in vector]
    finally:
        if own_client:
            await client.aclose()
