"""RAG HTTP API backed by a separately built SiliconFlow vector index.

Run directly (the Windows launchers and `rebuild_index.py --activate` do this):

    python rag/serve_cloud.py --port 8091

The index directory comes from `KB_CLOUD_INDEX` and defaults to
`rag/index-siliconflow/`.  `/health` reports the directory this process actually
loaded so the dashboard can show the serving index instead of guessing.
"""
from __future__ import annotations

import argparse
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import core
from siliconflow import MODEL, MODEL_ID, CloudEmbeddingError, SiliconFlowEmbedder, key_configured

core.INDEX_DIR = Path(os.environ.get('KB_CLOUD_INDEX', Path(__file__).resolve().parent / 'index-siliconflow')).resolve()
state = {'idx': None, 'emb': SiliconFlowEmbedder()}


@asynccontextmanager
async def lifespan(app):
    idx = core.Index.load()
    if not idx.ready or idx.meta.get('embed_model') != MODEL or idx.meta.get('embed_backend') != 'siliconflow':
        raise RuntimeError('cloud index missing or model mismatch')
    state['idx'] = idx
    yield


app = FastAPI(title='Denia RAG', docs_url=None, redoc_url=None, lifespan=lifespan)


class EmbedReq(BaseModel):
    text: str


class SearchReq(BaseModel):
    query: str
    top_k: int = 2
    margin: float | None = None
    gate: str | None = None
    category: str | None = None
    max_chars: int = 1100


@app.get('/health')
def health():
    idx = state['idx']
    return {'ready': bool(idx and idx.ready and key_configured()), 'chunks': idx.size if idx else 0,
            'dim': idx.meta.get('dim') if idx else None, 'gate_mode': core.GATE_MODE,
            'gate_margin': idx.gate_margin if idx else None, 'gate_stats': idx.meta.get('gate_stats') if idx else None,
            'entries': len(idx.lexicon) if idx else 0, 'embed_backend': 'siliconflow', 'provider': 'SiliconFlow',
            'model': MODEL, 'index': str(Path(core.INDEX_DIR).resolve()),
            'vector_mem_mb': round(idx.vectors.nbytes / 1048576, 1) if idx else 0,
            'sources': idx.meta.get('sources', []) if idx else []}


@app.post('/embed')
def embed(req: EmbedReq):
    if not req.text.strip() or len(req.text) > 2000:
        raise HTTPException(400, 'text must be 1-2000 characters')
    try:
        return {'model': MODEL_ID, 'vector': state['emb'].encode([req.text])[0].tolist()}
    except CloudEmbeddingError as exc:
        raise HTTPException(503, str(exc)) from None


@app.post('/search')
def do_search(req: SearchReq):
    start = time.time()
    stats = {}
    try:
        hits = core.search(state['idx'], req.query, state['emb'], top_k=req.top_k, margin=req.margin,
                           gate=req.gate, category=req.category, stats=stats)
    except CloudEmbeddingError as exc:
        return {'hits': [], 'error': str(exc)}
    picked, budget = [], req.max_chars
    for h in hits:
        body = h.text[:budget]
        picked.append(h.as_dict() | {'text': body})
        budget -= len(body)
        if budget <= 0:
            break
    return {'query': req.query, 'hits': picked, **stats, 'took_ms': round((time.time()-start)*1000, 1),
            'injected_chars': sum(len(h['text']) for h in picked)}


@app.post('/reindex')
def reindex():
    raise HTTPException(409, 'Build and validate a new cloud index offline before activation')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Serve the SiliconFlow RAG index on localhost.')
    parser.add_argument('--host', default=os.environ.get('KB_RAG_HOST', '127.0.0.1'))
    try:
        default_port = int(os.environ.get('KB_RAG_PORT', '8091'))
    except ValueError:
        default_port = 8091
    parser.add_argument('--port', type=int, default=default_port)
    parser.add_argument('--log-level', default=os.environ.get('KB_RAG_LOG_LEVEL', 'info'))
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
