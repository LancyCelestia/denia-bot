import json
from pathlib import Path

import parser_settings
import media_parser.core.event_log as event_log_module


def test_default_event_log_uses_bot_data_dir(monkeypatch):
    monkeypatch.setattr(event_log_module, '_log', None)
    log = event_log_module.event_log()
    expected = Path(event_log_module.__file__).resolve().parents[2] / 'data' / 'media_parser' / 'events.jsonl'
    assert log.path == expected


def test_panel_events_reads_bot_event_store(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({
        't': '2026-11-21 19:31:23',
        'ts': 1795260683.17,
        'kind': 'error',
        'reason': 'ActionFailed',
        'detail': 'highway upload error_code=323',
        'platform': 'xhs',
        'url': 'xhslink.com/m/87Pytnw022i',
    }, ensure_ascii=False) + '\n', encoding='utf-8')
    monkeypatch.setattr(event_log_module, '_log', event_log_module.ParserEventLog(path))

    data = parser_settings.events(limit=10)

    assert data['stats']['total'] == 1
    assert data['stats']['counts'] == {'error': 1}
    assert data['entries'][0]['reason'] == 'ActionFailed'
