import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import model_publication
import persona_settings

MODEL_TEXT = """relay:
  provider: openai
  default: true
  template: Denia
  template_mode: system
  model_name: test-model
  api_key: sk-secret-value
echo-test:
  provider: _echo
  default: false
  template_mode: system
  model_name: echo
"""


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    templates = tmp_path / 'templates'
    templates.mkdir()
    (templates / 'Denia.jinja2').write_text('denia', encoding='utf-8')
    (templates / 'Shorekeeper.jinja2').write_text('shorekeeper', encoding='utf-8')
    configs = tmp_path / 'configs'
    configs.mkdir()
    models = configs / 'models.yml'
    models.write_text(MODEL_TEXT, encoding='utf-8')
    model_publication.approve_existing(models, hashlib.sha256(models.read_bytes()).hexdigest())

    auth = tmp_path / 'auth'
    monkeypatch.setattr(d, 'AUTH_FILE', auth)
    d.set_password('a-strong-test-password')
    monkeypatch.setattr(d, 'session_store_path', lambda: tmp_path / 'sessions.json')
    monkeypatch.setattr(d, 'sessions', {})
    monkeypatch.setattr(persona_settings, 'MODELS_PATH', models)
    monkeypatch.setattr(persona_settings, 'TEMPLATES_DIR', templates)
    monkeypatch.setattr(persona_settings, 'STATE_PATH', tmp_path / 'no-state.json')
    monkeypatch.setattr(persona_settings, 'get_private', lambda key: '')
    return {'client': TestClient(d.app), 'models': models, 'templates': templates}


def login(client):
    response = client.post('/api/login', json={'password': 'a-strong-test-password'})
    assert response.status_code == 200
    return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf']}


def test_persona_requires_login(panel):
    assert panel['client'].get('/api/persona').status_code == 401


def test_persona_requires_csrf_and_valid_template(panel):
    client = panel['client']
    headers = login(client)
    assert client.post('/api/persona', json={'template': 'Shorekeeper'}).status_code == 403
    assert client.post('/api/persona', json={'preset': 'Shorekeeper'}).status_code == 403

    bad = client.post('/api/persona', json={'template': '../secret'}, headers=headers)
    assert bad.status_code == 400
    bad_preset = client.post('/api/persona', json={'preset': '../secret'}, headers=headers)
    assert bad_preset.status_code == 400
    assert client.post('/api/persona', json=[], headers=headers).status_code == 400
    assert not (panel['templates'] / 'current.jinja2').exists()
    assert model_publication.read_published(panel['models']) == panel['models'].read_bytes()


def test_persona_saves_and_is_visible(panel):
    client = panel['client']
    headers = login(client)

    view = client.get('/api/persona').json()
    assert view['active_model'] == 'relay'
    assert view['resolved']['template'] == 'Denia'
    assert {item['name'] for item in view['templates']} == {'Denia', 'Shorekeeper'}

    saved = client.post('/api/persona', json={'template': 'Shorekeeper', 'scope': 'all'},
                        headers=headers)
    assert saved.status_code == 200
    body = saved.json()
    assert body['changed'] == ['relay', 'echo-test']
    assert body['restart_required'] is True
    text = panel['models'].read_text(encoding='utf-8')
    assert 'api_key: sk-secret-value' in text
    assert text.count('template: Shorekeeper') == 2
    assert model_publication.read_published(panel['models']) == panel['models'].read_bytes()

    refreshed = client.get('/api/persona').json()
    assert refreshed['resolved']['path'].endswith('Shorekeeper.jinja2')


def test_persona_refuses_unpublished_configuration(panel):
    client = panel['client']
    headers = login(client)
    panel['models'].write_text(MODEL_TEXT + '# manual edit\n', encoding='utf-8')
    before = panel['models'].read_bytes()

    response = client.post('/api/persona', json={'template': 'Shorekeeper'}, headers=headers)

    assert response.status_code == 409
    assert panel['models'].read_bytes() == before


def test_persona_loads_preset_into_current(panel):
    client = panel['client']
    headers = login(client)
    preset_bytes = (panel['templates'] / 'Shorekeeper.jinja2').read_bytes()

    response = client.post('/api/persona', json={'preset': 'Shorekeeper', 'scope': 'all'},
                           headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body['loaded_preset'] == 'Shorekeeper'
    assert body['target_template'] == 'current'
    assert body['restart_required'] is True
    assert body['models_changed'] == ['relay', 'echo-test']
    current = panel['templates'] / 'current.jinja2'
    assert current.read_bytes() == preset_bytes
    assert (panel['templates'] / 'Shorekeeper.jinja2').read_bytes() == preset_bytes
    text = panel['models'].read_text(encoding='utf-8')
    assert text.count('template: current') == 2
    assert 'api_key: sk-secret-value' in text
    assert model_publication.read_published(panel['models']) == panel['models'].read_bytes()

    refreshed = client.get('/api/persona').json()
    assert refreshed['resolved']['template'] == 'current'
    assert refreshed['current']['exists'] is True
    assert refreshed['current']['safe'] is True
    assert {item['name'] for item in refreshed['templates']} == {'Denia', 'Shorekeeper'}


def test_persona_second_load_needs_no_restart(panel):
    client = panel['client']
    headers = login(client)
    first = client.post('/api/persona', json={'preset': 'Shorekeeper'}, headers=headers)
    assert first.status_code == 200

    second = client.post('/api/persona', json={'preset': 'Denia'}, headers=headers)

    assert second.status_code == 200
    body = second.json()
    assert body['models_changed'] == []
    assert body['restart_required'] is False
    assert (panel['templates'] / 'current.jinja2').read_bytes() == b'denia'


def test_persona_current_requires_login_and_csrf(panel):
    client = panel['client']
    assert client.get('/api/persona/current').status_code == 401
    assert client.post('/api/persona/current', json={'content': 'body'}).status_code == 401

    login(client)
    assert client.post('/api/persona/current', json={'content': 'body'}).status_code == 403
    assert not (panel['templates'] / 'current.jinja2').exists()
    assert model_publication.read_published(panel['models']) == panel['models'].read_bytes()


def test_persona_current_absent_is_empty(panel):
    login(panel['client'])

    view = panel['client'].get('/api/persona/current').json()

    assert view['current']['exists'] is False
    assert view['content'] == ''
    assert view['active_is_current'] is False


def test_persona_current_roundtrip_keeps_models_untouched(panel):
    client = panel['client']
    headers = login(client)
    body = '手写正文\n{{ ai_nickname }} 你好\n'
    before = panel['models'].read_bytes()

    saved = client.post('/api/persona/current', json={'content': body}, headers=headers)

    assert saved.status_code == 200
    result = saved.json()
    assert result['content'] == body
    assert result['restart_required'] is False
    assert result['active_is_current'] is False
    assert result['target_template'] == 'current'
    assert result['current']['exists'] is True
    assert result['current']['safe'] is True
    assert (panel['templates'] / 'current.jinja2').read_text(encoding='utf-8') == body
    assert panel['models'].read_bytes() == before
    assert model_publication.read_published(panel['models']) == before
    assert client.get('/api/persona').json()['resolved']['template'] == 'Denia'

    refreshed = client.get('/api/persona/current').json()
    assert refreshed['content'] == body
    assert refreshed['current']['sha256'] == result['current']['sha256']


@pytest.mark.parametrize('content', [
    '', '   ', 'a\x00b', '{{ broken',
    'x' * (persona_settings.MAX_CURRENT_BYTES + 1),
])
def test_persona_current_rejects_invalid_body_and_preserves_previous(panel, content):
    client = panel['client']
    headers = login(client)
    target = panel['templates'] / 'current.jinja2'
    target.write_text('previous body', encoding='utf-8')
    before = target.read_bytes()
    models_before = panel['models'].read_bytes()

    response = client.post('/api/persona/current', json={'content': content}, headers=headers)

    assert response.status_code == 400
    assert response.json()['detail']
    assert target.read_bytes() == before
    assert panel['models'].read_bytes() == models_before


def test_persona_current_rejects_non_string_and_non_object(panel):
    client = panel['client']
    headers = login(client)

    assert client.post('/api/persona/current', json={'content': 1}, headers=headers).status_code == 400
    assert client.post('/api/persona/current', json=[], headers=headers).status_code == 400
    assert not (panel['templates'] / 'current.jinja2').exists()


def test_persona_load_preset_refreshes_editor_content(panel):
    client = panel['client']
    headers = login(client)
    client.post('/api/persona', json={'preset': 'Shorekeeper', 'scope': 'all'}, headers=headers)

    view = client.get('/api/persona/current').json()

    assert view['content'] == 'shorekeeper'
    assert view['active_is_current'] is True
    assert view['current']['sha256'] == hashlib.sha256(b'shorekeeper').hexdigest()


def test_dashboard_html_has_raw_persona_editor():
    text = Path(d.__file__).with_name('dashboard.html').read_text(encoding='utf-8')

    for marker in ('id="personaBody"', 'id="personaBodySave"', 'id="personaBodyReload"',
                   '/api/persona/current', '保存当前人格', '刷新编辑内容',
                   'renderPersonaBodyMeta', 'savePersonaBody', 'loadPersonaBody'):
        assert marker in text


