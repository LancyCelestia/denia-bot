import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import model_pref_logic as mp
import model_pref_panel
import model_publication
import persona_settings

MODEL_TEXT = """# provider config
relay:
  provider: openai
  default: true
  template: Denia
  template_mode: system
  model_name: gemini-3.8-flash-high
  api_host: http://old.example/v1
  api_key: sk-secret-value
  stream: true
# diagnostic entry
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
    configs = tmp_path / 'configs'
    configs.mkdir()
    models = configs / 'models.yml'
    models.write_text(MODEL_TEXT, encoding='utf-8')
    model_publication.approve_existing(models, hashlib.sha256(models.read_bytes()).hexdigest())

    state = tmp_path / 'data' / 'model_pref.json'
    auth = tmp_path / 'auth'
    monkeypatch.setattr(d, 'AUTH_FILE', auth)
    d.set_password('a-strong-test-password')
    monkeypatch.setattr(d, 'session_store_path', lambda: tmp_path / 'sessions.json')
    monkeypatch.setattr(d, 'sessions', {})
    monkeypatch.setattr(persona_settings, 'MODELS_PATH', models)
    monkeypatch.setattr(persona_settings, 'TEMPLATES_DIR', templates)
    monkeypatch.setattr(persona_settings, 'get_private', lambda key: '')
    monkeypatch.setattr(model_pref_panel, 'STATE_PATH', state)
    return {'client': TestClient(d.app), 'models': models, 'templates': templates,
            'state': state, 'monkeypatch': monkeypatch}


def login(client):
    response = client.post('/api/login', json={'password': 'a-strong-test-password'})
    assert response.status_code == 200
    return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf']}


def seed_state(path, **over):
    state = mp.new_state()
    state.update(over)
    mp.save_state(path, state)
    return state


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=None):
        self.status_code = status_code
        self.payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode()

    def json(self):
        return self.payload


def test_model_pref_requires_login(panel):
    assert panel['client'].get('/api/model-pref').status_code == 401


def test_model_pref_post_requires_csrf(panel):
    client = panel['client']
    headers = login(client)
    assert client.post('/api/model-pref', json={'primary': 'm', 'secondary': None}).status_code == 403
    assert client.post('/api/model-pref/provider', json={'base_url': 'http://x/v1'}).status_code == 403
    assert client.post('/api/model-pref/upstream', json={}).status_code == 403
    assert client.post('/api/model-pref', json=[], headers=headers).status_code == 400
    assert client.post('/api/model-pref', json={}, headers=headers).status_code == 400
    assert client.post('/api/model-pref/provider', json=[], headers=headers).status_code == 400
    assert client.post('/api/model-pref/upstream', json=[], headers=headers).status_code == 400


def test_model_pref_view_hides_entries_and_key(panel):
    seed_state(panel['state'], primary='relay', primary_model='deepseek-v4.1-flash-eco',
               secondary=None, swaps={'relay': 2, 'echo-test': 5})
    client = panel['client']
    headers = login(client)
    view = client.get('/api/model-pref', headers=headers)
    assert view.status_code == 200
    body = view.json()
    encoded = json.dumps(body, ensure_ascii=False)
    assert body['provider'] == {
        'base_url': 'http://old.example/v1',
        'key_configured': True,
        'model_name': 'gemini-3.8-flash-high',
        'template': 'Denia',
    }
    assert body['active_model'] == 'deepseek-v4.1-flash-eco'
    assert body['active_source'] == 'model_pref'
    assert body['state']['primary_model'] == 'deepseek-v4.1-flash-eco'
    assert body['state']['swaps'] == {'gemini-3.8-flash-high': 2}
    assert body['publication']['published'] is True
    assert 'sk-secret-value' not in encoded
    assert 'relay' not in encoded
    assert 'echo-test' not in encoded


def test_model_pref_provider_save_and_restart(panel):
    seed_state(panel['state'], primary='relay', primary_model='deepseek-v4.1-flash-eco')
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref/provider',
                           json={'base_url': 'http://new.example/v1/', 'api_key': 'sk-new-key'},
                           headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body['restart_required'] is True
    assert body['provider']['base_url'] == 'http://new.example/v1'
    assert body['provider']['key_configured'] is True
    assert 'sk-new-key' not in response.text
    assert 'sk-secret-value' not in response.text
    data = yaml.safe_load(panel['models'].read_text(encoding='utf-8'))
    assert data['relay']['api_host'] == 'http://new.example/v1'
    assert data['relay']['api_key'] == 'sk-new-key'
    assert data['relay']['template'] == 'Denia'
    assert data['relay']['model_name'] == 'gemini-3.8-flash-high'
    assert data['echo-test']['model_name'] == 'echo'
    assert model_publication.read_published(panel['models']) == panel['models'].read_bytes()


def test_model_pref_provider_rejects_bad_url(panel):
    client = panel['client']
    headers = login(client)
    before = panel['models'].read_bytes()
    response = client.post('/api/model-pref/provider',
                           json={'base_url': 'ftp://example.com/v1'}, headers=headers)
    assert response.status_code == 400
    assert panel['models'].read_bytes() == before


def test_model_pref_provider_refuses_unpublished(panel):
    seed_state(panel['state'], primary='relay')
    panel['models'].write_text(MODEL_TEXT + '# manual edit\n', encoding='utf-8')
    models_before = panel['models'].read_bytes()
    state_before = panel['state'].read_bytes()
    client = panel['client']
    headers = login(client)
    view = client.get('/api/model-pref', headers=headers)
    assert view.status_code == 200
    assert view.json()['publication']['published'] is False
    response = client.post('/api/model-pref/provider',
                           json={'base_url': 'http://new.example/v1', 'api_key': 'sk-new'},
                           headers=headers)
    assert response.status_code == 409
    assert panel['models'].read_bytes() == models_before
    assert panel['state'].read_bytes() == state_before


def test_model_pref_upstream_returns_models_without_key(panel):
    seed_state(panel['state'], primary='relay')
    seen = {}

    def fake_get(url, **kwargs):
        seen['url'] = url
        seen.update(kwargs)
        return FakeResponse(payload={'data': [{'id': 'm1'}, {'id': 'm2'}]})

    panel['monkeypatch'].setattr(model_pref_panel.httpx, 'get', fake_get)
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref/upstream', json={}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body['models'] == ['m1', 'm2']
    assert body['source'] == 'http://old.example/v1'
    assert 'sk-secret-value' not in response.text
    assert seen['headers'] == {'Authorization': 'Bearer sk-secret-value'}


def test_model_pref_upstream_error_is_502_without_key(panel):
    seed_state(panel['state'], primary='relay')
    panel['monkeypatch'].setattr(
        model_pref_panel.httpx, 'get',
        lambda url, **kwargs: FakeResponse(status_code=500, payload={}),
    )
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref/upstream', json={}, headers=headers)
    assert response.status_code == 502
    assert 'sk-secret-value' not in response.text


def test_model_pref_models_save(panel):
    seed_state(panel['state'], primary='relay', primary_model='old-model',
               secondary=None, swaps={'relay': 3})
    models_before = panel['models'].read_bytes()
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': 'deepseek-v4.1-flash-eco', 'secondary': None},
                           headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body['saved']['restart_required'] is False
    assert body['active_model'] == 'deepseek-v4.1-flash-eco'
    assert 'relay' not in response.text
    assert 'echo-test' not in response.text
    state = mp.load_state(panel['state'])
    assert state['primary'] == 'relay'
    assert state['primary_model'] == 'deepseek-v4.1-flash-eco'
    assert state['secondary'] is None
    assert state['swaps'] == {'relay': 3}
    assert panel['models'].read_bytes() == models_before


@pytest.mark.parametrize('primary,secondary', [
    ('', None), ('a\x00b', None), ('x' * 201, None), (None, None), ('ok', 'a\nb'),
])
def test_model_pref_models_reject_bad_names(panel, primary, secondary):
    seed_state(panel['state'], primary='relay')
    before = panel['state'].read_bytes()
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': primary, 'secondary': secondary},
                           headers=headers)
    assert response.status_code == 400
    assert panel['state'].read_bytes() == before
