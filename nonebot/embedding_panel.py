"""Authenticated dashboard configuration for the SiliconFlow embedding key.

Also exposes the guarded RAG rebuild action.  A rebuild is expensive and uses
the SiliconFlow API, so the endpoint requires an explicit confirmation and runs
the builder as a separate process; the request itself only starts the job.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException, Request

STATE_FILE = 'rag-rebuild.json'
LOG_FILE = 'rag-rebuild.log'
SERVICE = 'qqbot-rag-cloud'


def _repo(root) -> Path:
    return Path(root).resolve().parent


def _active_index(root) -> Path | None:
    """Find the index path used by the systemd unit, with a local fallback."""
    configured = os.environ.get('KB_CLOUD_INDEX', '').strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else _repo(root) / path
    for unit in (Path('/etc/systemd/system/qqbot-rag-cloud.service'), Path('/lib/systemd/system/qqbot-rag-cloud.service')):
        try:
            text = unit.read_text(encoding='utf-8')
        except OSError:
            continue
        match = re.search(r'^\s*Environment\s*=\s*KB_CLOUD_INDEX=(\S+)', text, re.MULTILINE)
        if match:
            path = Path(match.group(1))
            return path if path.is_absolute() else _repo(root) / path
    rag = _repo(root) / 'rag'
    candidates = sorted(p for p in rag.glob('index-siliconflow*') if p.is_dir() and '.rebuild-' not in p.name)
    return candidates[-1] if candidates else None


def _profile_path(root) -> Path | None:
    configured = os.environ.get('RAG_PROFILE_CHUNKS', '').strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else _repo(root) / path
    candidates = sorted((_repo(root) / 'rag' / 'imports').glob('character-profiles-*/chunks.jsonl'))
    return candidates[-1] if candidates else None


def _line_count(path: Path) -> int:
    try:
        with path.open(encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def source_inventory(root) -> dict:
    repo = _repo(root)
    knowledge = repo / 'knowledge'
    files = sorted(
        p for p in list(knowledge.rglob('*.md')) + list(knowledge.rglob('*.txt'))
        if p.is_file()
    ) if knowledge.is_dir() else []
    sources = [
        {'name': p.relative_to(repo).as_posix(), 'bytes': p.stat().st_size, 'kind': 'knowledge'}
        for p in files
    ]
    profile = _profile_path(root)
    profile_count = 0
    profile_name = ''
    if profile and profile.is_file():
        profile_name = profile.relative_to(repo).as_posix()
        profile_count = _line_count(profile)
        sources.append({
            'name': profile_name,
            'bytes': profile.stat().st_size,
            'kind': 'profile',
            'chunks': profile_count,
        })
    return {
        'sources': sources,
        'knowledge': [p.relative_to(repo).as_posix() for p in files],
        'knowledge_count': len(files),
        'profile': profile_name,
        'profile_count': profile_count,
    }


def _read_state(root) -> dict:
    path = (Path(root) / 'data' / STATE_FILE)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(root, state: dict) -> None:
    path = Path(root) / 'data' / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _python_for(root) -> str:
    rag = _repo(root) / 'rag'
    for candidate in (rag / '.venv' / 'bin' / 'python', rag / '.venv' / 'Scripts' / 'python.exe'):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _key_configured(path: Path) -> bool:
    try:
        key = json.loads(path.read_text(encoding='utf-8')).get('api_key', '')
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(key, str) and bool(key.strip())


def _start_rebuild(root, *, sources: dict, active: Path | None, state: dict, log_path: Path) -> dict:
    repo = _repo(root)
    script = repo / 'rag' / 'rebuild_index.py'
    if not script.is_file():
        raise HTTPException(503, '重建脚本不存在：rag/rebuild_index.py')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    if active is not None:
        output = active.with_name(f'{active.name}.rebuild-{stamp}')
    else:
        output = repo / 'rag' / f'index-siliconflow-{stamp}'
    command = [
        _python_for(root), str(script),
        '--knowledge', str(repo / 'knowledge'),
        '--output', str(output),
        '--state', str(Path(root) / 'data' / STATE_FILE),
        '--batch', '16',
    ]
    profile = sources.get('profile')
    if profile:
        command += ['--profile', str(repo / profile)]
    if active is not None:
        command += [
            '--active', str(active),
            '--service', SERVICE,
            '--activate',
        ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open('ab', buffering=0)
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    kwargs = {
        'cwd': str(repo / 'rag'),
        'stdout': log_file,
        'stderr': subprocess.STDOUT,
        'env': env,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        log_file.close()
    state.update({
        'state': 'running',
        'phase': 'start',
        'message': '重建任务已启动',
        'started_at': time.time(),
        'pid': process.pid,
        'sources': [x['name'] for x in sources['sources']],
        'profile': sources.get('profile', ''),
        'index': str(active) if active is not None else '',
        'output': str(output),
        'service': SERVICE,
        'log': str(log_path.relative_to(repo)),
    })
    _write_state(root, state)
    return state


def register(app, *, authorized, mutate, root):
    path = root / 'data' / 'embedding_secrets.json'

    @app.get('/api/embedding')
    def embedding_status(req: Request):
        authorized(req)
        return {'provider': 'SiliconFlow', 'model': 'BAAI/bge-m3', 'configured': _key_configured(path)}

    @app.post('/api/embedding')
    async def embedding_save(req: Request):
        mutate(req)
        body = await req.json()
        key = body.get('api_key') if isinstance(body, dict) else None
        if not isinstance(key, str) or not key.startswith('sk-') or not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
            raise HTTPException(400, 'API Key 格式无效')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.embedding-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'api_key': key}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {'ok': True, 'configured': True}

    @app.get('/api/embedding/rebuild')
    def embedding_rebuild_status(req: Request):
        authorized(req)
        state = _read_state(root)
        if state.get('state') == 'running' and state.get('pid') and not _pid_alive(state.get('pid')):
            state.update(state='failed', phase='done', message='重建进程已退出，但没有写入完成状态', finished_at=time.time())
            _write_state(root, state)
        inventory = source_inventory(root)
        active = _active_index(root)
        return {
            'configured': _key_configured(path),
            'provider': 'SiliconFlow',
            'model': 'BAAI/bge-m3',
            'active_index': str(active) if active is not None else '',
            'service': SERVICE,
            'sources': inventory['sources'],
            'knowledge_count': inventory['knowledge_count'],
            'profile': inventory['profile'],
            'profile_count': inventory['profile_count'],
            'rebuild': state or {'state': 'idle', 'message': '尚未重建'},
        }

    @app.post('/api/embedding/rebuild')
    async def embedding_rebuild_start(req: Request):
        mutate(req)
        try:
            body = await req.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get('confirm') is not True:
            raise HTTPException(400, '重建 RAG 索引需要二次确认')
        if not _key_configured(path):
            raise HTTPException(409, '请先保存有效的硅基流动 API Key')
        inventory = source_inventory(root)
        if not inventory['sources']:
            raise HTTPException(409, 'knowledge/ 下没有可索引的 .md 或 .txt 文件')
        state = _read_state(root)
        if state.get('state') == 'starting' or (
            state.get('state') == 'running' and state.get('pid') and _pid_alive(state.get('pid'))
        ):
            raise HTTPException(409, '已有 RAG 索引重建任务在运行')
        state = {
            'state': 'starting',
            'phase': 'start',
            'message': '正在启动重建任务',
            'started_at': time.time(),
            'sources': [x['name'] for x in inventory['sources']],
        }
        _write_state(root, state)
        active = _active_index(root)
        try:
            state = _start_rebuild(root, sources=inventory, active=active, state=state,
                                   log_path=Path(root) / 'data' / LOG_FILE)
        except HTTPException:
            state.update(state='failed', phase='done', message='重建任务启动失败', finished_at=time.time())
            _write_state(root, state)
            raise
        except Exception as exc:
            state.update(state='failed', phase='done', message=f'重建任务启动失败：{exc}', finished_at=time.time())
            _write_state(root, state)
            raise HTTPException(503, f'无法启动重建任务：{exc}') from exc
        return {'ok': True, 'rebuild': state}
