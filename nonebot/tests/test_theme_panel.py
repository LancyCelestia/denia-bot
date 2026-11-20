import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import theme_panel


TOKEN = 'theme-panel-test-token'
AUTH = {'x-test-auth': TOKEN}
MUTATE = {
    'x-test-auth': TOKEN,
    'x-test-csrf': TOKEN,
    'Origin': 'http://testserver',
}
HTML = Path(__file__).resolve().parents[1] / 'dashboard.html'


def make_app(root):
    app = FastAPI()

    def authorized(req):
        if req.headers.get('x-test-auth') != TOKEN:
            raise HTTPException(401, '请登录')

    def mutate(req):
        authorized(req)
        if req.headers.get('x-test-csrf') != TOKEN:
            raise HTTPException(403, '请求来源无效')
        if req.headers.get('origin') != 'http://testserver':
            raise HTTPException(403, '请求来源无效')

    theme_panel.register(app, authorized=authorized, mutate=mutate, root=root)
    return app


def valid_config():
    return theme_panel.default_config()


def preset_path(root):
    return Path(root) / 'data' / 'theme-presets.json'


def test_routes_registered(tmp_path):
    app = make_app(tmp_path)
    paths = {route.path for route in app.routes}
    assert '/api/theme/presets' in paths
    assert '/api/theme/presets/apply' in paths
    assert '/api/theme/presets/delete' in paths


def test_list_requires_auth_and_contains_builtin_presets(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        assert client.get('/api/theme/presets').status_code == 401
        response = client.get('/api/theme/presets', headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data['count'] == 4
    assert set(data['names']) == set(theme_panel.BUILTIN_PRESETS)
    assert data['invalid'] == []
    assert all(item['builtin'] is True and item['config'] for item in data['presets'])


def test_save_apply_delete_round_trip_and_atomic_write(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '我的夜间', 'config': valid_config(), 'overwrite': False}
        saved = client.post('/api/theme/presets', json=body, headers=MUTATE)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {'name': '我的夜间', 'builtin': False}

        path = preset_path(tmp_path)
        assert path.is_file()
        raw = path.read_text(encoding='utf-8')
        assert '我的夜间' in raw and '我的夜间' in raw  # ensure_ascii=False
        assert json.loads(raw)['presets']['我的夜间']['misc']['radius'] is None
        assert not list(path.parent.glob('*.tmp'))
        if os.name != 'nt':
            assert path.stat().st_mode & 0o777 == 0o600

        listed = client.get('/api/theme/presets', headers=AUTH).json()
        assert '我的夜间' in listed['names']
        assert any(item['name'] == '我的夜间' and item['builtin'] is False for item in listed['presets'])

        applied = client.post('/api/theme/presets/apply', json={'name': '我的夜间'}, headers=MUTATE)
        assert applied.status_code == 200
        assert applied.json()['config'] == valid_config()

        deleted = client.post('/api/theme/presets/delete', json={'name': '我的夜间'}, headers=MUTATE)
        assert deleted.status_code == 200
        assert '我的夜间' not in client.get('/api/theme/presets', headers=AUTH).json()['names']
        assert '我的夜间' not in json.loads(path.read_text(encoding='utf-8'))['presets']


def test_apply_does_not_write_active_config(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=MUTATE)
    assert response.status_code == 200
    assert response.json()['config']['skin'] == 'default'
    assert not (tmp_path / 'data' / 'theme_config.json').exists()


def test_write_requires_csrf_and_origin(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': 'x', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=AUTH).status_code == 403
        no_origin = {'x-test-auth': TOKEN, 'x-test-csrf': TOKEN}
        assert client.post('/api/theme/presets', json=body, headers=no_origin).status_code == 403
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=AUTH).status_code == 403
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=no_origin).status_code == 403


def test_overwrite_is_explicit(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '同名', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=MUTATE).status_code == 200
        duplicate = client.post('/api/theme/presets', json=body, headers=MUTATE)
        assert duplicate.status_code == 409
        overwrite = client.post('/api/theme/presets', json={**body, 'overwrite': True}, headers=MUTATE)
        assert overwrite.status_code == 200


def test_builtin_names_are_reserved(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '暖纸默认', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=MUTATE).status_code == 409
        assert client.post('/api/theme/presets/delete', json={'name': '暖纸默认'}, headers=MUTATE).status_code == 409
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=MUTATE).status_code == 200


@pytest.mark.parametrize('name', ['', '   ', 'a/b', 'a\\b', '.hidden', 'hidden.', 'a\nb', 'a:b', 'a' * 33])
def test_invalid_names_are_rejected(tmp_path, name):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/theme/presets', json={'name': name, 'config': valid_config()}, headers=MUTATE)
    assert response.status_code in (400, 422)


@pytest.mark.parametrize('changes', [
    {'mode': 'sepia'},
    {'skin': 'unknown'},
    {'style': 'huge'},
    {'misc': {'radius': 25}},
    {'misc': {'radius': -1}},
    {'misc': {'radius': 1.5}},
    {'misc': {'sidebarLayout': 'overlay'}},
    {'misc': {'iconColor': 'red'}},
    {'background': {'kind': 'unknown'}},
    {'background': {'preset': 'unknown'}},
    {'background': {'scope': 'wide'}},
    {'background': {'kind': 'url', 'value': 'file:///tmp/a.png'}},
    {'background': {'kind': 'upload', 'value': '/etc/passwd'}},
    {'background': {'kind': 'url', 'value': 'https://example.com/a"b'}},
    {'background': {'kind': 'url', 'value': 'http://[::1'}},
    {'background': {'kind': 'url', 'value': 'https://[::1]:bad'}},
])
def test_invalid_configs_are_rejected(tmp_path, changes):
    config = valid_config()
    config.update(changes)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/theme/presets', json={'name': 'bad', 'config': config}, headers=MUTATE)
    assert response.status_code == 400, response.text


def test_config_and_file_size_limits(tmp_path, monkeypatch):
    with TestClient(make_app(tmp_path)) as client:
        monkeypatch.setattr(theme_panel, 'MAX_CONFIG_BYTES', 10)
        response = client.post('/api/theme/presets', json={'name': 'big', 'config': valid_config()}, headers=MUTATE)
        assert response.status_code == 400
    with TestClient(make_app(tmp_path)) as client:
        monkeypatch.setattr(theme_panel, 'MAX_CONFIG_BYTES', 4096)
        monkeypatch.setattr(theme_panel, 'MAX_FILE_BYTES', 10)
        response = client.post('/api/theme/presets', json={'name': 'big', 'config': valid_config()}, headers=MUTATE)
        assert response.status_code == 409


def test_preset_count_limit(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        for index in range(theme_panel.MAX_PRESETS):
            response = client.post(
                '/api/theme/presets',
                json={'name': f'p{index}', 'config': valid_config()},
                headers=MUTATE,
            )
            assert response.status_code == 200
        response = client.post('/api/theme/presets', json={'name': 'overflow', 'config': valid_config()}, headers=MUTATE)
    assert response.status_code == 409


def test_corrupt_file_is_reported_and_not_overwritten(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{not json', encoding='utf-8')
    with TestClient(make_app(tmp_path)) as client:
        response = client.get('/api/theme/presets', headers=AUTH)
        saved = client.post('/api/theme/presets', json={'name': 'x', 'config': valid_config()}, headers=MUTATE)
    assert response.status_code == 500
    assert '主题预设文件' in response.json()['detail']
    assert saved.status_code == 500
    assert path.read_text(encoding='utf-8') == '{not json'


def test_invalid_user_entry_is_listed_but_not_applied(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 1, 'presets': {'坏预设': {'mode': 'sepia'}}}, ensure_ascii=False), encoding='utf-8')
    with TestClient(make_app(tmp_path)) as client:
        listed = client.get('/api/theme/presets', headers=AUTH)
        applied = client.post('/api/theme/presets/apply', json={'name': '坏预设'}, headers=MUTATE)
    assert listed.status_code == 200
    data = listed.json()
    assert data['invalid'] == ['坏预设']
    assert '坏预设' in data['names']
    assert all(item['name'] != '坏预设' for item in data['presets'])
    assert applied.status_code == 500


def test_dashboard_html_has_four_tabs_and_keeps_existing_ids():
    text = HTML.read_text(encoding='utf-8')
    for tab in ('presets', 'appearance', 'background', 'misc'):
        assert f'data-appearance-tab="{tab}"' in text
    for marker in (
        'id="appearancePopover"',
        'id="appearanceButton"',
        'id="appearanceButtonMobile"',
        'name="appearanceMode"',
        'name="appearanceSkin"',
        'name="appearanceStyle"',
        'id="appearanceBackgroundPreset"',
        'id="appearanceBackgroundUrl"',
        'id="appearanceBackgroundFile"',
        'id="appearanceBgState"',
        'id="themePresetName"',
        'id="themePresetList"',
        'id="appearanceRadius"',
        'name="appearanceSidebarLayout"',
        'name="appearanceIconColor"',
        'aria-label="主题设置"',
    ):
        assert marker in text
    assert text.count('id="appearancePopover"') == 1
    assert text.count('id="appearanceButton"') == 1
