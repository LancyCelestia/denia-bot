"""Windows-only tests for tools/snowluma-path.ps1."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell only')

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / 'tools' / 'snowluma-path.ps1'


def _powershell() -> str:
    exe = shutil.which('powershell')
    if not exe:
        pytest.skip('powershell.exe is not available')
    return exe


def _runtime(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'node.exe').write_bytes(b'')
    (root / 'index.mjs').write_bytes(b'')


def _run(root: Path, tmp_path: Path, *args: str, env_home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop('SNOWLUMA_HOME', None)
    env.pop('QQBOT_SNOWLUMA', None)
    user_profile = tmp_path / 'userprofile'
    user_profile.mkdir(exist_ok=True)
    env['USERPROFILE'] = str(user_profile)
    if env_home is not None:
        env['SNOWLUMA_HOME'] = str(env_home)
    return subprocess.run(
        [_powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HELPER), '-Root', str(root), *args],
        capture_output=True,
        text=True,
        encoding='mbcs',
        errors='replace',
        env=env,
        timeout=120,
    )


def test_env_home_wins(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    home = tmp_path / '雪' / 'env-home'
    _runtime(home)

    result = _run(root, tmp_path, env_home=home)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == home.resolve()
    assert (root / 'logs' / 'snowluma-home.txt').read_text(encoding='utf-8') == str(home.resolve())
    assert (root / 'logs' / 'snowluma-home.ansi.txt').read_bytes().decode('mbcs') == str(home.resolve())


def test_explicit_path(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    home = tmp_path / 'explicit'
    _runtime(home)

    result = _run(root, tmp_path, '-SnowLumaPath', str(home))

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == home.resolve()


def test_sibling_fallback(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    home = tmp_path / 'SnowLuma'
    _runtime(home)

    result = _run(root, tmp_path)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == home.resolve()


def test_zip_fallback_preserves_existing_config(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    existing = root / 'snowluma' / 'config'
    existing.mkdir(parents=True)
    (existing / 'keep.txt').write_text('keep', encoding='utf-8')
    archive = root / 'SnowLuma-v2.4.2-win-x64.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('SnowLuma/node.exe', b'')
        zf.writestr('SnowLuma/index.mjs', b'')
        zf.writestr('SnowLuma/config/default.json', b'{}')

    result = _run(root, tmp_path)

    assert result.returncode == 0, result.stderr
    target = root / 'snowluma'
    assert Path(result.stdout.strip()).resolve() == target.resolve()
    assert (target / 'node.exe').is_file()
    assert (target / 'index.mjs').is_file()
    assert (existing / 'keep.txt').read_text(encoding='utf-8') == 'keep'
    assert not (existing / 'default.json').exists()


def test_missing_runtime_fails(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()

    result = _run(root, tmp_path)

    assert result.returncode == 1
    assert 'SnowLuma runtime is missing' in result.stderr
    assert not (root / 'logs' / 'snowluma-home.txt').exists()


def test_no_extract_blocks_zip(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    archive = root / 'SnowLuma-v2.4.2-win-x64.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('SnowLuma/node.exe', b'')
        zf.writestr('SnowLuma/index.mjs', b'')

    result = _run(root, tmp_path, '-NoExtract')

    assert result.returncode == 1
    assert 'NoExtract' in result.stderr
    assert not (root / 'snowluma' / 'node.exe').exists()
