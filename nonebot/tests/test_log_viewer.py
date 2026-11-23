import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import log_viewer


def _roots(tmp_path):
    project = tmp_path / 'project'
    nonebot = project / 'nonebot'
    (project / 'logs').mkdir(parents=True)
    (nonebot / 'logs').mkdir(parents=True)
    return project, nonebot


def test_reads_tail_and_masks_secrets(tmp_path):
    project, nonebot = _roots(tmp_path)
    (project / 'logs' / 'nonebot.out.log').write_text(
        'first\n'
        'Authorization: Bearer abcdefghijk\n'
        'api_key=secret123 token=abc123\n'
        'last\n',
        encoding='utf-8',
    )
    data = log_viewer.read_log('nonebot.out.log', tail=3, project_root=project, nonebot_root=nonebot)
    assert data['available'] is True
    assert data['path'] == 'logs/nonebot.out.log'
    assert data['lines'][-1] == 'last'
    assert 'Bearer ***' in data['lines'][0]
    assert 'api_key=***' in data['lines'][1]
    assert 'secret123' not in '\n'.join(data['lines'])
    assert 'abc123' not in '\n'.join(data['lines'])


def test_large_file_only_returns_tail(tmp_path):
    project, nonebot = _roots(tmp_path)
    path = project / 'logs' / 'nonebot.out.log'
    path.write_text('\n'.join(f'line {i}' for i in range(5000)) + '\n', encoding='utf-8')
    data = log_viewer.read_log('nonebot.out.log', tail=200, project_root=project, nonebot_root=nonebot)
    assert len(data['lines']) == 200
    assert data['lines'][-1] == 'line 4999'
    assert data['truncated'] is True


def test_rejects_traversal_and_unknown_names(tmp_path):
    project, nonebot = _roots(tmp_path)
    for name in ('../../etc/passwd', '..\\..\\secret', 'foo.log', ''):
        with pytest.raises(ValueError):
            log_viewer.read_log(name, project_root=project, nonebot_root=nonebot)


def test_daily_log_uses_newest_file(tmp_path):
    project, nonebot = _roots(tmp_path)
    daily = nonebot / 'logs'
    (daily / '2026-11-21.log').write_text('old\n', encoding='utf-8')
    (daily / '2026-11-22.log').write_text('new\n', encoding='utf-8')
    data = log_viewer.read_log('nonebot.log', tail=10, project_root=project, nonebot_root=nonebot)
    assert data['available'] is True
    assert data['path'] == 'nonebot/logs/2026-11-22.log'
    assert data['lines'] == ['new']


def test_missing_optional_log_is_not_an_error(tmp_path):
    project, nonebot = _roots(tmp_path)
    data = log_viewer.read_log('nonebot.err.log', project_root=project, nonebot_root=nonebot)
    assert data['available'] is False
    assert data['lines'] == []
    assert data['size'] == 0


def test_sources_reports_availability(tmp_path):
    project, nonebot = _roots(tmp_path)
    (project / 'logs' / 'nonebot.out.log').write_text('out\n', encoding='utf-8')
    (nonebot / 'logs' / '2026-11-22.log').write_text('daily\n', encoding='utf-8')
    result = {item['name']: item for item in log_viewer.sources(project_root=project, nonebot_root=nonebot)}
    assert result['nonebot.log']['available'] is True
    assert result['nonebot.out.log']['available'] is True
    assert result['nonebot.err.log']['available'] is False


def test_mask_secrets_covers_common_shapes():
    line = 'Cookie: session=abc; Authorization: Bearer tokenvalue sk-abcdefgh1234'
    masked = log_viewer.mask_secrets(line)
    assert 'session=abc' not in masked
    assert 'tokenvalue' not in masked
    assert 'sk-abcdefgh1234' not in masked
