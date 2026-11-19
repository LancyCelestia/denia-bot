"""Start/stop fixed external services whose plugin switch was changed.

Only the pinned gsuid_core service/container is touched.  No user-provided
command, unit name, or process path is accepted.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GSUID_DIR = ROOT / 'gsuid' / 'core'
GSUID_UNIT = 'qqbot-gsuid-core.service'
GSUID_CONTAINER = 'gsuid_core'
GSUID_PID_FILE = ROOT / 'logs' / 'gsuid-core.pid'
GSUID_PORT = 8765
log = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    """A fixed external service could not be changed."""


def _run(command, timeout=30):
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def _port_open(port=GSUID_PORT):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def _systemd_unit_exists():
    if not shutil.which('systemctl'):
        return False
    try:
        return _run(['systemctl', 'cat', GSUID_UNIT], timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _docker_container_exists():
    if not shutil.which('docker'):
        return False
    try:
        result = _run(['docker', 'inspect', '--format', '{{.State.Running}}', GSUID_CONTAINER], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() in {'true', 'false'}


def _docker_running():
    try:
        result = _run(['docker', 'inspect', '--format', '{{.State.Running}}', GSUID_CONTAINER], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == 'true'


def _windows_stop():
    pid = None
    try:
        raw = GSUID_PID_FILE.read_text(encoding='ascii').strip()
        if raw.isdigit() and 0 < int(raw) < 2**31:
            pid = int(raw)
    except OSError:
        pass
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        + (f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\";"
           f"if($p -and $p.CommandLine -match 'gsuid[\\\\/]core'){{taskkill.exe /PID {pid} /T /F | Out-Null}};" if pid else '')
        + "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
          "$_.CommandLine -and $_.CommandLine -match 'gsuid[\\\\/]core' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force };"
    )
    try:
        _run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceError('无法停止 Windows gsuid_core 进程') from exc
    finally:
        try:
            GSUID_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    if _port_open():
        raise ServiceError('gsuid_core 停止后仍在监听 8765')
    return {'service': 'gsuid_core', 'managed': True, 'backend': 'windows', 'running': False}


def _windows_start():
    if _port_open():
        return {'service': 'gsuid_core', 'managed': True, 'backend': 'windows', 'running': True}
    python = GSUID_DIR / '.venv' / 'Scripts' / 'python.exe'
    if not python.is_file():
        return {'service': 'gsuid_core', 'managed': False,
                'warning': 'gsuid_core 未安装（缺少 gsuid/core/.venv）'}
    logs = ROOT / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    try:
        with (logs / 'gsuid.out.log').open('ab') as out, (logs / 'gsuid.err.log').open('ab') as err:
            process = subprocess.Popen(
                [str(python), '-m', 'gsuid_core.core', '--host', '0.0.0.0'],
                cwd=str(GSUID_DIR), stdout=out, stderr=err, creationflags=flags,
                env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'),
            )
        GSUID_PID_FILE.write_text(str(process.pid), encoding='ascii')
    except OSError as exc:
        raise ServiceError('无法启动 Windows gsuid_core') from exc
    return {'service': 'gsuid_core', 'managed': True, 'backend': 'windows', 'running': True}


def stop_gsuid(platform=None):
    """Stop the pinned gsuid_core service/container/process."""
    platform = platform or sys.platform
    if platform.startswith('win'):
        return _windows_stop()
    if _systemd_unit_exists():
        try:
            result = _run(['systemctl', 'disable', '--now', GSUID_UNIT], timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ServiceError('无法调用 systemctl 停止 gsuid_core') from exc
        if result.returncode:
            raise ServiceError('systemctl disable --now gsuid_core 失败')
        return {'service': 'gsuid_core', 'managed': True, 'backend': 'systemd', 'running': False}
    if _docker_container_exists():
        if _docker_running():
            try:
                result = _run(['docker', 'stop', GSUID_CONTAINER], timeout=60)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ServiceError('无法调用 docker 停止 gsuid_core') from exc
            if result.returncode:
                raise ServiceError('停止 gsuid_core Docker 容器失败')
        return {'service': 'gsuid_core', 'managed': True, 'backend': 'docker', 'running': False}
    return {'service': 'gsuid_core', 'managed': False, 'warning': '未发现 gsuid_core systemd 服务或 Docker 容器'}


def start_gsuid(platform=None):
    """Start the pinned gsuid_core service/container/process."""
    platform = platform or sys.platform
    if platform.startswith('win'):
        return _windows_start()
    if _systemd_unit_exists():
        try:
            result = _run(['systemctl', 'enable', '--now', GSUID_UNIT], timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ServiceError('无法调用 systemctl 启动 gsuid_core') from exc
        if result.returncode:
            raise ServiceError('systemctl enable --now gsuid_core 失败')
        return {'service': 'gsuid_core', 'managed': True, 'backend': 'systemd', 'running': True}
    if _docker_container_exists():
        try:
            result = _run(['docker', 'start', GSUID_CONTAINER], timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ServiceError('无法调用 docker 启动 gsuid_core') from exc
        if result.returncode:
            raise ServiceError('启动 gsuid_core Docker 容器失败')
        return {'service': 'gsuid_core', 'managed': True, 'backend': 'docker', 'running': True}
    return {'service': 'gsuid_core', 'managed': False,
            'warning': '未发现 gsuid_core systemd 服务或 Docker 容器'}


def apply_plugin_change(plugin_id, enabled):
    """Apply the fixed service mapping for a plugin switch, if any."""
    if plugin_id != 'gsuid':
        return None
    try:
        return start_gsuid() if enabled else stop_gsuid()
    except (ServiceError, OSError, subprocess.SubprocessError) as exc:
        log.warning('gsuid service change failed: %s', exc)
        return {'service': 'gsuid_core', 'managed': False, 'warning': str(exc)}
