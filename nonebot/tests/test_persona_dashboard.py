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

    bad = client.post('/api/persona', json={'template': '../secret'}, headers=headers)
    assert bad.status_code == 400
    assert client.post('/api/persona', json=[], headers=headers).status_code == 400
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
