"""Process control for restarting the cloud RAG service without systemd.

`rebuild_index.py --activate` uses these helpers on Windows (and on POSIX when
systemd is unavailable): find the process listening on 8091, stop it, then start
`serve_cloud.py` detached with the new index.  The module is standard-library
only so it can run in the dashboard's Python environment, and every side effect
is a small function that tests can patch.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

NETSTAT_LINE = re.compile(r'^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$', re.IGNORECASE)


def _run(args: list[str], timeout: float = 20) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout if result.returncode == 0 else ''


def listening_pids(port: int) -> list[int]:
    port = int(port)
    if os.name == 'nt':
        output = _run(['netstat', '-ano', '-p', 'tcp'])
        return sorted({int(pid) for found, pid in NETSTAT_LINE.findall(output) if int(found) == port})
    output = _run(['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'])
    return sorted({int(item) for item in output.split() if item.isdigit()})


def port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if os.name == 'nt':
        _run(['taskkill', '/PID', str(pid), '/T', '/F'])
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def stop_listener(port: int, timeout: float = 15) -> list[int]:
    """Stop every process listening on `port`; raise if the port stays busy."""
    pids = listening_pids(port)
    for pid in pids:
        kill_pid(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_open(port):
            return pids
        time.sleep(0.2)
    if not pids:
        raise RuntimeError(f'port {port} is still in use but no listener PID was found')
    raise RuntimeError(f'port {port} still has listening process(es) after stop: {pids}')


def rag_python(repo: Path) -> str:
    """Prefer the dedicated RAG venv, then the NoneBot venv, then this Python."""
    repo = Path(repo)
    candidates = (
        repo / 'rag' / '.venv' / 'Scripts' / 'python.exe',
        repo / 'rag' / '.venv' / 'bin' / 'python',
        repo / 'nonebot' / '.venv' / 'Scripts' / 'python.exe',
        repo / 'nonebot' / '.venv' / 'bin' / 'python',
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def start_cloud_service(repo: Path, *, port: int, index: Path, log_dir: Path | None = None) -> subprocess.Popen:
    """Start `serve_cloud.py` detached, with the active index in its environment."""
    repo = Path(repo).resolve()
    script = repo / 'rag' / 'serve_cloud.py'
    if not script.is_file():
        raise RuntimeError(f'serve_cloud.py is missing: {script}')
    port = int(port)
    log_dir = Path(log_dir).resolve() if log_dir is not None else repo / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        'KB_CLOUD_INDEX': str(Path(index).resolve()),
        'KB_RAG_PORT': str(port),
        'PYTHONUNBUFFERED': '1',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
    })
    command = [rag_python(repo), str(script), '--port', str(port)]
    kwargs = {
        'cwd': str(repo / 'rag'),
        'env': env,
        'stdout': None,
        'stderr': None,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs['start_new_session'] = True
    out_path = log_dir / 'rag.out.log'
    err_path = log_dir / 'rag.err.log'
    with out_path.open('ab') as out, err_path.open('ab') as err:
        kwargs['stdout'] = out
        kwargs['stderr'] = err
        return subprocess.Popen(command, **kwargs)
