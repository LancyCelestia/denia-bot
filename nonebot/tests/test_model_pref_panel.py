import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import model_pref_logic as mp
import model_pref_panel
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
    return {'client': TestClient(d.app), 'models': models, 'templates': templates, 'state': state}


def login(client):
    response = client.post('/api/login', json={'password': 'a-strong-test-password'})
    assert response.status_code == 200
    return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf']}


def seed_state(path, **over):
    state = mp.new_state()
    state.update(over)
    mp.save_state(path, state)
    return state


def test_model_pref_requires_login(panel):
    assert panel['client'].get('/api/model-pref').status_code == 401


def test_model_pref_post_requires_csrf_and_both_keys(panel):
    client = panel['client']
    headers = login(client)
    assert client.post('/api/model-pref', json={'primary': None, 'secondary': None}).status_code == 403
    assert client.post('/api/model-pref', json=[], headers=headers).status_code == 400
    assert client.post('/api/model-pref', json={}, headers=headers).status_code == 400
    assert client.post('/api/model-pref', json={'primary': 'relay'}, headers=headers).status_code == 400
    assert not panel['state'].exists()


def test_model_pref_view_lists_entries_without_secrets(panel):
    seed_state(panel['state'], primary='relay', primary_model='upstream-a')
    client = panel['client']
    headers = login(client)
    view = client.get('/api/model-pref', headers=headers)
    assert view.status_code == 200
    body = view.json()
    by_name = {item['name']: item for item in body['models']}
    assert by_name['relay']['model_name'] == 'test-model'
    assert by_name['relay']['default'] is True
    assert by_name['echo-test']['model_name'] == 'echo'
    assert body['default_model'] == 'relay'
    assert body['active_model'] == 'relay'
    assert body['active_source'] == 'model_pref'
    assert body['state']['primary_model'] == 'upstream-a'
    assert body['publication']['published'] is True
    assert 'sk-secret-value' not in view.text


def test_model_pref_rejects_unknown_entry_and_keeps_state(panel):
    seed_state(panel['state'], primary='relay', secondary='echo-test')
    before = panel['state'].read_bytes()
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': 'missing', 'secondary': 'echo-test'},
                           headers=headers)
    assert response.status_code == 400
    assert panel['state'].read_bytes() == before
    assert client.post('/api/model-pref',
                       json={'primary': ['relay'], 'secondary': None},
                       headers=headers).status_code == 400


def test_model_pref_preserves_override_for_unchanged_entry(panel):
    seed_state(panel['state'], primary='relay', primary_model='upstream-a',
               secondary='echo-test', secondary_model='upstream-b',
               swaps={'relay': 2})
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': 'relay', 'secondary': 'echo-test'},
                           headers=headers)
    assert response.status_code == 200
    state = mp.load_state(panel['state'])
    assert state['primary'] == 'relay'
    assert state['primary_model'] == 'upstream-a'
    assert state['secondary'] == 'echo-test'
    assert state['secondary_model'] == 'upstream-b'
    assert state['swaps'] == {'relay': 2}
    assert state['updated']
    assert response.json()['saved']['restart_required'] is False


def test_model_pref_clears_override_when_entry_changes(panel):
    seed_state(panel['state'], primary='relay', primary_model='upstream-a',
               secondary='echo-test', secondary_model='upstream-b')
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': 'echo-test', 'secondary': None},
                           headers=headers)
    assert response.status_code == 200
    state = mp.load_state(panel['state'])
    assert state['primary'] == 'echo-test'
    assert state['primary_model'] is None
    assert state['secondary'] is None
    assert state['secondary_model'] is None
    body = response.json()
    assert body['active_model'] == 'echo-test'
    assert body['active_source'] == 'model_pref'


def test_model_pref_clears_primary_and_keeps_swaps(panel):
    seed_state(panel['state'], primary='relay', primary_model='upstream-a',
               swaps={'relay': 3})
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': '', 'secondary': 'echo-test'},
                           headers=headers)
    assert response.status_code == 200
    state = mp.load_state(panel['state'])
    assert state['primary'] is None
    assert state['primary_model'] is None
    assert state['secondary'] == 'echo-test'
    assert state['swaps'] == {'relay': 3}
    assert response.json()['active_model'] == 'relay'
    assert response.json()['active_source'] == 'default'


def test_model_pref_refuses_unpublished_models(panel):
    seed_state(panel['state'], primary='relay')
    panel['models'].write_text(MODEL_TEXT + '# manual edit\n', encoding='utf-8')
    models_before = panel['models'].read_bytes()
    state_before = panel['state'].read_bytes()
    client = panel['client']
    headers = login(client)

    view = client.get('/api/model-pref', headers=headers)
    assert view.status_code == 200
    assert view.json()['publication']['published'] is False

    response = client.post('/api/model-pref',
                           json={'primary': 'echo-test', 'secondary': None},
                           headers=headers)
    assert response.status_code == 409
    assert panel['state'].read_bytes() == state_before
    assert panel['models'].read_bytes() == models_before


def test_model_pref_save_does_not_touch_models(panel):
    seed_state(panel['state'], primary='relay')
    models_before = panel['models'].read_bytes()
    client = panel['client']
    headers = login(client)
    response = client.post('/api/model-pref',
                           json={'primary': 'echo-test', 'secondary': None},
                           headers=headers)
    assert response.status_code == 200
    assert panel['models'].read_bytes() == models_before
    assert 'sk-secret-value' not in json.dumps(response.json())
