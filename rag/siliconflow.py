"""SiliconFlow embedding client shared by the RAG service and index builder.

The free `BAAI/bge-m3` model is the only supported embedding backend.  Requests
are bounded by `KB_EMBED_TIMEOUT` (seconds, default 20) and transient provider
failures (network errors, 429 and 5xx) get a small number of retries.  Provider
errors are logged and included in the raised error instead of being swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

MODEL = 'BAAI/bge-m3'
MODEL_ID = 'siliconflow-bge-m3-v1'
API_URL = 'https://api.siliconflow.cn/v1/embeddings'
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
RETRY_BASE_SECONDS = 0.75
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
KEY_FILE = Path(os.environ.get('KB_EMBED_KEY_FILE', Path(__file__).resolve().parent.parent / 'nonebot' / 'data' / 'embedding_secrets.json'))
log = logging.getLogger(__name__)


class CloudEmbeddingError(RuntimeError):
    pass


def _timeout() -> float:
    try:
        value = float(os.environ.get('KB_EMBED_TIMEOUT', DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _retries() -> int:
    try:
        value = int(os.environ.get('KB_EMBED_RETRIES', DEFAULT_RETRIES))
    except (TypeError, ValueError):
        return DEFAULT_RETRIES
    return max(0, min(value, 5))


def read_api_key() -> str:
    try:
        key = json.loads(KEY_FILE.read_text(encoding='utf-8'))['api_key']
    except (OSError, ValueError, KeyError, TypeError):
        raise CloudEmbeddingError('embedding API key is not configured') from None
    if not isinstance(key, str) or not key.strip():
        raise CloudEmbeddingError('embedding API key is not configured')
    return key.strip()


def key_configured() -> bool:
    try:
        read_api_key()
    except CloudEmbeddingError:
        return False
    return True


def _http_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode('utf-8', 'replace')
    except Exception:  # noqa: BLE001 - an error body is best-effort diagnostics
        body = ''
    body = ' '.join(body.split())[:200]
    return f': {body}' if body else ''


def _post(payload: bytes, key: str, timeout: float) -> list[dict]:
    """POST one batch, retrying transient provider/network failures."""
    retries = _retries()
    last_error = ''
    for attempt in range(retries + 1):
        req = urllib.request.Request(API_URL, data=payload,
                                     headers={'Authorization': 'Bearer ' + key,
                                              'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
            try:
                data = json.loads(body)['data']
            except (TypeError, ValueError, KeyError):
                raise CloudEmbeddingError('invalid embedding response') from None
            if not isinstance(data, list):
                raise CloudEmbeddingError('invalid embedding response')
            return data
        except urllib.error.HTTPError as exc:
            detail = _http_detail(exc)
            last_error = f'embedding provider HTTP {exc.code}{detail}'
            if exc.code in TRANSIENT_HTTP and attempt < retries:
                log.warning('%s; retrying in %.2fs', last_error, RETRY_BASE_SECONDS * (2 ** attempt))
                time.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                continue
            log.error('%s', last_error)
            raise CloudEmbeddingError(last_error) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            last_error = f'embedding provider unavailable: {reason}'
            if attempt < retries:
                log.warning('%s; retrying in %.2fs', last_error, RETRY_BASE_SECONDS * (2 ** attempt))
                time.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                continue
            log.error('%s', last_error)
            raise CloudEmbeddingError(last_error) from None
    raise CloudEmbeddingError(last_error or 'embedding provider unavailable')


class SiliconFlowEmbedder:
    backend = 'siliconflow'
    active_provider = 'SiliconFlow'

    def encode(self, texts: list[str], batch: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        key = read_api_key()
        timeout = _timeout()
        results = []
        for start in range(0, len(texts), min(batch, 16)):
            part = texts[start:start + min(batch, 16)]
            payload = json.dumps({'model': MODEL, 'input': part, 'encoding_format': 'float'}, ensure_ascii=False).encode()
            data = _post(payload, key, timeout)
            try:
                if len(data) != len(part) or sorted(r['index'] for r in data) != list(range(len(part))):
                    raise ValueError('bad index')
                vectors = np.asarray([r['embedding'] for r in sorted(data, key=lambda r: r['index'])], dtype=np.float32)
                if vectors.shape != (len(part), 1024) or not np.isfinite(vectors).all():
                    raise ValueError('bad vector')
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if (norms == 0).any():
                    raise ValueError('zero vector')
                results.append(vectors / norms)
            except (KeyError, TypeError, ValueError, OverflowError):
                raise CloudEmbeddingError('invalid embedding response') from None
        return np.concatenate(results)
