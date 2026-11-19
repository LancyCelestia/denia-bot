"""Build a fresh SiliconFlow RAG index from the tracked knowledge corpus.

The dashboard's Embedding card invokes this as a detached process.  The active
index remains available while the new directory is built; activation stops the
RAG unit, swaps the directory atomically, starts it again, and rolls back if the
new index does not become healthy.

Examples (run from the repository root):
    rag/.venv/bin/python rag/rebuild_index.py --dry-run \
        --knowledge knowledge --output rag/index-siliconflow-rebuild
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402
from siliconflow import MODEL, CloudEmbeddingError, SiliconFlowEmbedder  # noqa: E402

STATE_VERSION = 1


def _atomic_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _update_state(path: Path | None, **changes) -> None:
    if path is None:
        return
    try:
        current = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update(changes)
    _atomic_json(path, current)


def discover_knowledge(directory: Path) -> list[Path]:
    """Return every markdown/text file under the corpus directory, recursively."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise RuntimeError(f'knowledge directory does not exist: {directory}')
    files = sorted(
        p for p in list(directory.rglob('*.md')) + list(directory.rglob('*.txt'))
        if p.is_file()
    )
    if not files:
        raise RuntimeError(f'no .md or .txt files under: {directory}')
    return files


def load_profile_chunks(path: Path | None, start_id: int) -> list[core.Chunk]:
    """Load the optional character-profile JSONL used by the existing cloud index."""
    if path is None:
        return []
    path = Path(path).resolve()
    if not path.is_file():
        raise RuntimeError(f'profile corpus does not exist: {path}')
    chunks: list[core.Chunk] = []
    with path.open(encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            text = row.get('embedding_text') or row.get('text')
            if not isinstance(text, str) or not text.strip():
                continue
            character = str(row.get('character') or '').strip()
            sections = row.get('section_path') or []
            if isinstance(sections, str):
                sections = [sections]
            path_label = ' > '.join([x for x in [character, *map(str, sections)] if x])
            chunks.append(core.Chunk(
                id=start_id + len(chunks),
                text=text,
                path=path_label,
                source=path.name,
                category='角色档案',
            ))
    return chunks


def _profile_entries(lexicon: dict[str, list[int]], chunks: list[core.Chunk], start_id: int) -> None:
    for chunk in chunks[start_id:]:
        name = core.norm_entry(chunk.path.split(' > ')[0])
        if name:
            lexicon.setdefault(name, []).append(chunk.id)
    for key, ids in lexicon.items():
        lexicon[key] = sorted(set(ids))


def _encode_with_retry(embedder, texts: list[str], attempts: int = 5) -> np.ndarray:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return embedder.encode(texts)
        except CloudEmbeddingError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise last or RuntimeError('embedding failed')


def _wait_for_health(url: str, expected_chunks: int, seconds: int = 60) -> dict:
    last_error = 'no health response'
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                data = json.load(response)
            if data.get('ready') and int(data.get('chunks') or 0) == expected_chunks:
                return data
            last_error = f"ready={data.get('ready')} chunks={data.get('chunks')}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc) or type(exc).__name__
        time.sleep(1)
    raise RuntimeError(f'RAG health check failed: {last_error}')


def activate_index(output: Path, active: Path, service: str, health_url: str, expected_chunks: int) -> str:
    """Swap a validated index into the active path and restart the RAG unit."""
    systemctl = shutil.which('systemctl')
    if os.name == 'nt' or systemctl is None or not service:
        return 'index built but not activated (systemctl unavailable)'

    active = Path(active)
    previous = active.with_name(active.name + f'.previous-{int(time.time())}')
    failed = active.with_name(active.name + f'.failed-{int(time.time())}')
    moved_active = False
    switched = False
    try:
        subprocess.run([systemctl, 'stop', service], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if active.exists() or active.is_symlink():
            os.replace(active, previous)
            moved_active = True
        os.replace(output, active)
        switched = True
        subprocess.run([systemctl, 'start', service], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _wait_for_health(health_url, expected_chunks)
        return f'activated {active.name} (previous: {previous.name})'
    except Exception:
        subprocess.run([systemctl, 'stop', service], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            if switched and (active.exists() or active.is_symlink()):
                os.replace(active, failed)
            if moved_active and previous.exists():
                os.replace(previous, active)
        finally:
            subprocess.run([systemctl, 'start', service], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raise


def build_index(
    *,
    knowledge: Path,
    profile: Path | None,
    output: Path,
    state: Path | None,
    active: Path | None,
    service: str,
    health_url: str,
    activate: bool,
    batch: int = 16,
    limit: int | None = None,
) -> dict:
    output = Path(output).resolve()
    knowledge_files = discover_knowledge(knowledge)
    profile = Path(profile).resolve() if profile else None
    _update_state(state, version=STATE_VERSION, state='running', phase='prepare',
                  message='读取语料并分块', done=0, total=0, index=str(output),
                  sources=[str(p) for p in knowledge_files] + ([str(profile)] if profile else []))

    chunks = core.load_chunks(knowledge_files)
    knowledge_count = len(chunks)
    if not chunks:
        raise RuntimeError('knowledge corpus produced no chunks')
    profile_chunks = load_profile_chunks(profile, knowledge_count)
    chunks.extend(profile_chunks)
    if limit:
        chunks = chunks[:max(1, int(limit))]
    total = len(chunks)

    tokens = [core.tokenize(c.text) for c in chunks]
    bm25 = core.BM25([c.id for c in chunks], tokens)
    lexicon, lexicon_rev = core.build_lexicon(
        chunks[:knowledge_count], knowledge_files, bm25.df,
    )
    _profile_entries(lexicon, chunks, knowledge_count)

    _update_state(
        state, phase='embedding', message=f'编码 {total} 个块', done=0, total=total,
        knowledge_files=len(knowledge_files), knowledge_chunks=knowledge_count,
        profile_chunks=len(profile_chunks),
    )

    embedder = SiliconFlowEmbedder()
    vectors = np.zeros((total, 1024), dtype=np.float32)
    for start in range(0, total, batch):
        part = [c.text for c in chunks[start:start + batch]]
        vectors[start:start + len(part)] = _encode_with_retry(embedder, part)
        done = start + len(part)
        _update_state(state, phase='embedding', done=done, total=total,
                      message=f'已编码 {done}/{total} 个块')

    idx = core.Index()
    idx.chunks = {c.id: c for c in chunks}
    idx.vectors = vectors
    idx.bm25 = bm25
    idx.lexicon = lexicon
    idx.lex_rev = lexicon_rev
    idx.meta = {
        'dim': int(vectors.shape[1]),
        'count': total,
        'embed_backend': 'siliconflow',
        'embed_model': MODEL,
        'entries': len(lexicon),
        'knowledge_files': len(knowledge_files),
        'profile_chunks': len(profile_chunks),
        'sources': [str(p) for p in knowledge_files] + ([str(profile)] if profile else []),
    }
    _update_state(state, phase='calibrate', message='校准检索闸门')
    idx.calibrate(embedder)

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f'rebuild output already exists: {output}')
    output.mkdir(parents=True)
    core.INDEX_DIR = output
    _update_state(state, phase='save', message='写入新索引')
    idx.save(docs=tokens)

    core.INDEX_DIR = output
    check = core.Index.load()
    if not check.ready or check.size != total or check.meta.get('embed_backend') != 'siliconflow' or check.meta.get('embed_model') != MODEL:
        raise RuntimeError('new index validation failed')

    activated = False
    activated_message = 'index built; activation disabled'
    if activate and active is not None:
        _update_state(state, phase='activate', message='切换索引并重启 RAG 服务')
        activated_message = activate_index(output, active, service, health_url, total)
        activated = activated_message.startswith('activated')
    _update_state(
        state, state='success', phase='done', done=total, total=total,
        message='重建完成：' + activated_message, activated=activated,
        active_index=str(active) if active is not None else '',
        finished_at=time.time(), entries=len(lexicon),
    )
    return {'count': total, 'entries': len(lexicon), 'output': str(output), 'activated': activated}


def main() -> int:
    parser = argparse.ArgumentParser(description='Rebuild the SiliconFlow RAG index from knowledge/')
    parser.add_argument('--knowledge', default=str(HERE.parent / 'knowledge'))
    parser.add_argument('--profile', help='optional character-profile chunks.jsonl')
    parser.add_argument('--output', required=True)
    parser.add_argument('--state', help='dashboard job state JSON')
    parser.add_argument('--active', help='active index path to replace')
    parser.add_argument('--service', default='qqbot-rag-cloud')
    parser.add_argument('--health-url', default='http://127.0.0.1:8091/health')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--limit', type=int, help='only embed the first N chunks (smoke test)')
    parser.add_argument('--dry-run', action='store_true', help='print corpus information without embedding')
    args = parser.parse_args()

    state = Path(args.state) if args.state else None
    try:
        if args.dry_run:
            files = discover_knowledge(Path(args.knowledge))
            rows = load_profile_chunks(Path(args.profile) if args.profile else None, 0)
            print(json.dumps({'knowledge': [str(p) for p in files], 'profile_chunks': len(rows)}, ensure_ascii=False))
            return 0
        if args.batch < 1 or args.batch > 16:
            raise RuntimeError('--batch must be 1..16')
        result = build_index(
            knowledge=Path(args.knowledge),
            profile=Path(args.profile) if args.profile else None,
            output=Path(args.output),
            state=state,
            active=Path(args.active) if args.active else None,
            service=args.service,
            health_url=args.health_url,
            activate=args.activate,
            batch=args.batch,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        _update_state(state, state='failed', message=str(exc), finished_at=time.time())
        print(f'[!] {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
