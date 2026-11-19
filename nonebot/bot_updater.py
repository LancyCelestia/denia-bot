"""受控地检查并快进更新当前部署的 Git 检出。"""
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    """可展示给面板的更新失败，不包含 Git 原始输出。"""

    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


class BotUpdater:
    def __init__(self, repo=None, *, git='git', timeout=120):
        self.repo = Path(repo) if repo is not None else ROOT
        self.git = git
        self.timeout = timeout
        self._lock = threading.Lock()

    def _git_available(self):
        return (self.repo / '.git').exists() and shutil.which(self.git) is not None

    def _run(self, *args, timeout=None):
        if not self._git_available():
            raise UpdateError('NOT_A_GIT_CHECKOUT', '当前目录不是可更新的 Git 检出')
        env = dict(os.environ)
        env.update({
            'GIT_TERMINAL_PROMPT': '0',
            'GCM_INTERACTIVE': 'Never',
            'PYTHONIOENCODING': 'utf-8',
        })
        try:
            result = subprocess.run(
                [self.git, *args], cwd=str(self.repo), capture_output=True,
                text=True, encoding='utf-8', errors='replace',
                timeout=timeout or self.timeout, env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise UpdateError('TIMEOUT', '更新超时，请稍后重试') from exc
        except OSError as exc:
            raise UpdateError('GIT_UNAVAILABLE', '无法调用 Git，请检查服务器环境') from exc
        if result.returncode:
            log.warning('git %s failed with code %s', args[0] if args else '?', result.returncode)
            raise UpdateError('GIT_FAILED', 'Git 操作失败，请检查检出目录和远端连接')
        return result.stdout.strip()

    def _status_locked(self):
        if not self._git_available():
            return {'available': False, 'reason': 'NOT_A_GIT_CHECKOUT'}
        try:
            branch = self._run('symbolic-ref', '--short', 'HEAD')
            head = self._run('rev-parse', 'HEAD')
            upstream = self._run('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
            dirty = bool(self._run('status', '--porcelain', '--untracked-files=no'))
        except UpdateError as exc:
            return {'available': False, 'reason': exc.code}
        return {
            'available': True,
            'branch': branch,
            'head': head,
            'short': head[:10],
            'upstream': upstream,
            'dirty': dirty,
        }

    def status(self):
        """只读本地状态；不访问远端。"""
        with self._lock:
            return self._status_locked()

    def _check_locked(self):
        info = self._status_locked()
        if not info['available']:
            raise UpdateError(info['reason'])
        upstream = info['upstream']
        remote = upstream.split('/', 1)[0]
        self._run('fetch', '--prune', remote, timeout=45)
        counts = self._run('rev-list', '--left-right', '--count', f'HEAD...{upstream}')
        try:
            ahead, behind = (int(value) for value in counts.split())
        except (TypeError, ValueError) as exc:
            raise UpdateError('GIT_FAILED', '无法读取远端分支状态') from exc
        changed = self._run('diff', '--name-only', f'HEAD...{upstream}').splitlines()
        commits = self._run(
            'log', '--format=%h %s', '--max-count=20', f'HEAD..{upstream}'
        ).splitlines()
        info.update({
            'ahead': ahead,
            'behind': behind,
            'changed': changed[:30],
            'commits': commits,
            'requirements_changed': 'requirements.lock' in changed,
            'migration_changed': any(
                path.startswith('nonebot/migrate_db') or '/migrations/' in path
                for path in changed
            ),
            'can_apply': not info['dirty'] and ahead == 0 and behind > 0,
        })
        return info

    def check(self):
        """拉取远端元数据并报告是否可快进；不修改工作区。"""
        with self._lock:
            return self._check_locked()

    def apply(self):
        """拒绝本地改动和分叉，只执行 git pull --ff-only。"""
        with self._lock:
            info = self._check_locked()
            if info['dirty']:
                raise UpdateError('LOCAL_CHANGES', '工作区有未提交的改动，已拒绝更新')
            if info['ahead']:
                raise UpdateError('DIVERGED', '本地分支有领先提交，已拒绝非快进更新')
            if info['behind'] == 0:
                return {**info, 'updated': False}
            previous = info['head']
            self._run('pull', '--ff-only', timeout=self.timeout)
            head = self._run('rev-parse', 'HEAD')
            return {
                **info,
                'updated': True,
                'previous': previous,
                'head': head,
                'short': head[:10],
                'can_apply': False,
            }
