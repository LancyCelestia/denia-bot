import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bilibili_panel
import dashboard


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO meta VALUES('source_stop','0');
CREATE TABLE subscriptions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,bot TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
 uid TEXT NOT NULL,name TEXT NOT NULL,baseline REAL NOT NULL,last_check REAL NOT NULL DEFAULT 0,
 last_error TEXT NOT NULL DEFAULT '',gap INTEGER NOT NULL DEFAULT 0,
 UNIQUE(bot,kind,target,uid));
CREATE TABLE deliveries(
 sub_id INTEGER NOT NULL,dynamic_id TEXT NOT NULL,pub_ts REAL NOT NULL,payload TEXT,
 status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
 receipt TEXT,note TEXT NOT NULL DEFAULT '',PRIMARY KEY(sub_id,dynamic_id));
"""


def make_state(root, subs=(), deliveries=(), *, stop=False, version=31):
    path = Path(root) / 'data' / 'bilibili_dynamic' / 'state.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        db.execute("UPDATE meta SET value=? WHERE key='source_stop'", ('1' if stop else '0',))
        for sub in subs:
            db.execute('INSERT INTO subscriptions(bot,kind,target,uid,name,baseline,last_check,last_error,gap) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',
                       (sub['bot'], sub['kind'], sub['target'], sub['uid'], sub['name'],
                        sub.get('baseline', 100.0), sub.get('last_check', 0.0),
                        sub.get('last_error', ''), int(sub.get('gap', False))))
        db.executemany('INSERT INTO deliveries(sub_id,dynamic_id,pub_ts,status) VALUES(?,?,?,?)', deliveries)
        db.execute(f'PRAGMA user_version={int(version)}')
        db.commit()
    finally:
        db.close()
    return path


def sub(uid, *, bot='10001', kind='group', target='20001', name='UP主', **changes):
    return {'bot': bot, 'kind': kind, 'target': target, 'uid': uid, 'name': name, **changes}


@pytest.fixture
def clean_env(monkeypatch):
    for key in ('BILI_ENABLED', 'BILI_INTERVAL', 'BILI_TARGETS', 'BILI_STATE_FILE'):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_route_requires_login():
    with TestClient(dashboard.app) as client:
        assert client.get('/api/bilibili').status_code == 401


def test_missing_database_reports_uninitialized(tmp_path, clean_env):
    clean_env.setenv('BILI_ENABLED', 'true')
    view = bilibili_panel.view(tmp_path)
    assert view['available'] is False
    assert '未启用' in view['reason']
    assert view['config'] == {'enabled': True, 'interval': None}


def test_view_groups_subscriptions_by_session(tmp_path, clean_env):
    make_state(tmp_path, [
        sub('476092297', target='20001', name='甲', baseline=100.0, last_check=500.0),
        sub('999', target='20001', name='乙', baseline=110.0, last_check=400.0,
            last_error='SCAN_GAP', gap=True),
        sub('476092297', kind='private', target='30001', name='甲', baseline=120.0,
            last_check=450.0, last_error='REMOTE_RESTRICTED'),
    ], [(1, 'd1', 1.0, 'sent'), (1, 'd2', 2.0, 'pending'), (1, 'd3', 3.0, 'baseline'),
        (2, 'd4', 4.0, 'failed'), (3, 'd5', 5.0, 'sent')])
    view = bilibili_panel.view(tmp_path)
    assert view['available'] is True
    assert view['source_stopped'] is False
    assert view['checked_at'] == 500.0
    assert view['totals']['sessions'] == 2
    assert view['totals']['subscriptions'] == 3
    assert view['totals']['uids'] == 2
    assert view['totals']['deliveries']['sent'] == 2
    assert view['totals']['deliveries']['failed'] == 1
    group, private = view['sessions']
    assert (group['kind'], group['target']) == ('group', '20001')
    assert [row['uid'] for row in group['subscriptions']] == ['476092297', '999']
    assert group['deliveries'] == {'pending': 1, 'retry': 0, 'sending': 0, 'unknown': 0,
                                   'sent': 1, 'failed': 1, 'skipped': 0}
    assert group['subscriptions'][0]['error'] == ''
    assert group['subscriptions'][1]['error'] == '动态检查不完整，可能遗漏'
    assert group['subscriptions'][1]['gap'] is True
    assert private['subscriptions'][0]['error'] == 'B站访问受限，来源已暂停'
    assert private['deliveries']['sent'] == 1


def test_view_treats_names_as_plain_text(tmp_path, clean_env):
    make_state(tmp_path, [sub('1', name='a\x00b\u202e' + '长' * 120)], [(1, 'd1', 1.0, 'sent')])
    name = bilibili_panel.view(tmp_path)['sessions'][0]['subscriptions'][0]['name']
    assert name.startswith('ab')
    assert '\x00' not in name
    assert len(name) == 80


def test_view_flags_sessions_missing_from_configured_targets(tmp_path, clean_env):
    make_state(tmp_path, [sub('1'), sub('2', kind='private', target='30001')],
               [(1, 'd1', 1.0, 'sent'), (2, 'd2', 2.0, 'sent')])
    clean_env.setenv('BILI_TARGETS', '["10001:group:20001"]')
    view = bilibili_panel.view(tmp_path)
    assert [row['target_allowed'] for row in view['sessions']] == [True, False]
    clean_env.setenv('BILI_TARGETS', 'not json')
    assert [row['target_allowed'] for row in bilibili_panel.view(tmp_path)['sessions']] == [None, None]


def test_state_path_follows_dotenv_and_environment(tmp_path, clean_env):
    (tmp_path / '.env').write_text('# x\nBILI_STATE_FILE=data/alt.sqlite3\n', encoding='utf-8')
    assert bilibili_panel.state_path(tmp_path) == tmp_path / 'data' / 'alt.sqlite3'
    clean_env.setenv('BILI_STATE_FILE', str(tmp_path / 'env.sqlite3'))
    assert bilibili_panel.state_path(tmp_path) == tmp_path / 'env.sqlite3'


def test_unsupported_schema_is_reported(tmp_path, clean_env):
    make_state(tmp_path, [sub('1')], [(1, 'd1', 1.0, 'sent')], version=99)
    with pytest.raises(HTTPException) as error:
        bilibili_panel.view(tmp_path)
    assert error.value.status_code == 503
    assert '99' in error.value.detail


def test_view_never_writes_to_the_state_database(tmp_path, clean_env):
    path = make_state(tmp_path, [sub('1', last_error='DEADLINE')], [(1, 'd1', 1.0, 'pending')])
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    bilibili_panel.view(tmp_path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (path.parent / 'state.sqlite3-journal').exists()


def test_sidebar_keeps_primary_order_and_groups_bot_settings():
    text = (Path(dashboard.__file__).with_name('dashboard.html')).read_text(encoding='utf-8')
    nav = re.search(r'<nav class="nav".*?</nav>', text, re.S).group(0)
    links = re.findall(r'<a [^>]*href="#([^"]+)"', nav)
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    basics_links = re.findall(r'<a [^>]*href="#([^"]+)"', basics)
    sections = [name for name in re.findall(r'<section id="([^"]+)"', text)
                if name not in {'login', 'firstRun', 'changePassword'}]
    assert links[:6] == ['overview', 'bot-info', 'memes', 'memory', 'group', 'status']
    assert basics_links == ['bot-info', 'persona', 'tokens', 'master', 'superusers']
    assert 'bilibili' in links
    assert set(links) | set(basics_links) == set(sections) | {'overview'}


def test_target_route_requires_csrf_and_forwards_target(tmp_path, clean_env, monkeypatch):
    auth_file = tmp_path / 'auth.json'
    auth_file.write_text(
        '{"salt":"00000000000000000000000000000000","hash":"x","must_change":false}',
        encoding='utf-8')
    token = 'panel-test-token'
    monkeypatch.setattr(dashboard, 'AUTH_FILE', auth_file)
    monkeypatch.setattr(dashboard, 'sessions', {token: time.time() + 600})
    seen = {}

    def fake_update(root, **kwargs):
        seen.update(kwargs)
        return ['10001:group:20001']

    monkeypatch.setattr(bilibili_panel, 'update_targets', fake_update)
    headers = {'origin': 'http://testserver', 'x-csrf-token': token}
    payload = {'action': 'add', 'bot': '10001', 'kind': 'group', 'target': '20001'}
    with TestClient(dashboard.app) as client:
        assert client.post('/api/bilibili/targets', json=payload).status_code == 401
        client.cookies.set('admin_session', token)
        response = client.post('/api/bilibili/targets', json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == {'targets': ['10001:group:20001'], 'restart_required': True}
    assert seen == payload


def test_target_update_preserves_env_and_exposes_targets(tmp_path, clean_env):
    env_path = tmp_path / '.env'
    env_path.write_text('KEEP=1\nexport BILI_TARGETS=["10001:group:20001"]\nOTHER=2\n',
                        encoding='utf-8')
    targets = bilibili_panel.update_targets(
        tmp_path, action='add', bot='10001', kind='group', target='20002')
    assert targets == ['10001:group:20001', '10001:group:20002']
    text = env_path.read_text(encoding='utf-8')
    assert text.startswith('KEEP=1\nexport BILI_TARGETS=')
    assert 'OTHER=2\n' in text
    assert not list(tmp_path.glob('.env-bili-*'))

    view = bilibili_panel.view(tmp_path)
    assert view['targets'] == ['10001:group:20001', '10001:group:20002']
    assert view['targets_editable'] is True

    targets = bilibili_panel.update_targets(
        tmp_path, action='remove', bot='10001', kind='group', target='20001')
    assert targets == ['10001:group:20002']


def test_target_update_rejects_invalid_ids(tmp_path, clean_env):
    with pytest.raises(ValueError):
        bilibili_panel.update_targets(
            tmp_path, action='add', bot='10001', kind='group', target='not-a-group')
    with pytest.raises(ValueError):
        bilibili_panel.update_targets(
            tmp_path, action='add', bot='10001', kind='channel', target='20001')


def test_target_update_refuses_process_env_override(tmp_path, clean_env):
    clean_env.setenv('BILI_TARGETS', '["10001:group:20001"]')
    view = bilibili_panel.view(tmp_path)
    assert view['targets'] == ['10001:group:20001']
    assert view['targets_editable'] is False
    with pytest.raises(HTTPException) as error:
        bilibili_panel.update_targets(
            tmp_path, action='add', bot='10001', kind='group', target='20002')
    assert error.value.status_code == 409
    assert not (tmp_path / '.env').exists()
