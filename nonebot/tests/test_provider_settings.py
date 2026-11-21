import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

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
def config(tmp_path, monkeypatch):
    templates = tmp_path / 'templates'
    templates.mkdir()
    (templates / 'Denia.jinja2').write_text('denia', encoding='utf-8')
    configs = tmp_path / 'configs'
    configs.mkdir()
    models = configs / 'models.yml'
    models.write_text(MODEL_TEXT, encoding='utf-8')
    model_publication.approve_existing(models, hashlib.sha256(models.read_bytes()).hexdigest())
    state = tmp_path / 'data' / 'model_pref.json'
    monkeypatch.setattr(persona_settings, 'MODELS_PATH', models)
    monkeypatch.setattr(persona_settings, 'TEMPLATES_DIR', templates)
    monkeypatch.setattr(persona_settings, 'get_private', lambda key: '')
    monkeypatch.setattr(model_pref_panel, 'STATE_PATH', state)
    return SimpleNamespace(models=models, state=state, templates=templates)


def seed_state(path, **over):
    state = mp.new_state()
    state.update(over)
    mp.save_state(path, state)
    return state


def test_provider_view_hides_entries_and_key(config):
    seed_state(config.state, primary='relay', primary_model='deepseek-v4.1-flash-eco',
               secondary=None, swaps={'relay': 2, 'echo-test': 7})
    view = model_pref_panel.view()
    encoded = json.dumps(view, ensure_ascii=False)
    assert view['provider']['base_url'] == 'http://old.example/v1'
    assert view['provider']['key_configured'] is True
    assert view['provider']['model_name'] == 'gemini-3.8-flash-high'
    assert view['provider']['template'] == 'Denia'
    assert view['active_model'] == 'deepseek-v4.1-flash-eco'
    assert view['active_source'] == 'model_pref'
    assert view['state']['primary_model'] == 'deepseek-v4.1-flash-eco'
    assert view['state']['swaps'] == {'gemini-3.8-flash-high': 2}
    assert view['publication']['published'] is True
    assert 'sk-secret-value' not in encoded
    assert 'relay' not in encoded
    assert 'echo-test' not in encoded


def test_save_provider_replaces_base_url_and_key_without_touching_other_bytes(config):
    state_before = seed_state(config.state, primary='relay',
                              primary_model='deepseek-v4.1-flash-eco')
    result = model_pref_panel.save_provider('http://new.example/v1/', 'sk-new-key')
    assert result['restart_required'] is True
    assert result['provider']['base_url'] == 'http://new.example/v1'
    assert result['provider']['key_configured'] is True
    assert 'sk-new-key' not in json.dumps(result)
    assert 'sk-secret-value' not in json.dumps(result)

    text = config.models.read_text(encoding='utf-8')
    data = yaml.safe_load(text)
    assert data['relay']['api_host'] == 'http://new.example/v1'
    assert data['relay']['api_key'] == 'sk-new-key'
    assert data['relay']['template'] == 'Denia'
    assert data['relay']['model_name'] == 'gemini-3.8-flash-high'
    assert data['echo-test']['model_name'] == 'echo'
    assert '# provider config' in text
    assert '# diagnostic entry' in text
    assert model_publication.read_published(config.models) == config.models.read_bytes()
    assert mp.load_state(config.state) == state_before


def test_save_provider_blank_key_keeps_existing(config):
    seed_state(config.state, primary='relay', primary_model='deepseek-v4.1-flash-eco')
    result = model_pref_panel.save_provider('http://new.example/v1', '   ')
    assert result['restart_required'] is True
    data = yaml.safe_load(config.models.read_text(encoding='utf-8'))
    assert data['relay']['api_host'] == 'http://new.example/v1'
    assert data['relay']['api_key'] == 'sk-secret-value'
    assert 'sk-secret-value' not in json.dumps(result)


@pytest.mark.parametrize('url', [
    '', 'ftp://example.com/v1', 'http:///v1', 'http://user:pass@example.com/v1',
    'http://example.com/v1?x=1', 'http://example.com/v1#x', 'not a url',
])
def test_save_provider_rejects_bad_url(config, url):
    before = config.models.read_bytes()
    with pytest.raises(ValueError):
        model_pref_panel.save_provider(url, 'sk-new-key')
    assert config.models.read_bytes() == before


def test_save_provider_refuses_unpublished(config):
    seed_state(config.state, primary='relay')
    config.models.write_text(MODEL_TEXT + '# manual edit\n', encoding='utf-8')
    models_before = config.models.read_bytes()
    state_before = config.state.read_bytes()
    with pytest.raises(model_pref_panel.Unpublished):
        model_pref_panel.save_provider('http://new.example/v1', 'sk-new-key')
    assert config.models.read_bytes() == models_before
    assert config.state.read_bytes() == state_before


def test_save_models_writes_primary_secondary_and_keeps_swaps(config):
    seed_state(config.state, primary='relay', primary_model='old-model',
               secondary=None, swaps={'relay': 3})
    models_before = config.models.read_bytes()
    result = model_pref_panel.save_models('deepseek-v4.1-flash-eco', None)
    assert result['saved']['restart_required'] is False
    assert result['active_model'] == 'deepseek-v4.1-flash-eco'
    state = mp.load_state(config.state)
    assert state['primary'] == 'relay'
    assert state['primary_model'] == 'deepseek-v4.1-flash-eco'
    assert state['secondary'] is None
    assert state['secondary_model'] is None
    assert state['swaps'] == {'relay': 3}
    assert state['updated']
    assert config.models.read_bytes() == models_before


def test_save_models_canonicalizes_default_model(config):
    seed_state(config.state, primary='relay', primary_model='old-model')
    result = model_pref_panel.save_models('gemini-3.8-flash-high', 'gemini-3.8-flash-high')
    state = mp.load_state(config.state)
    assert state['primary'] == 'relay'
    assert state['primary_model'] is None
    assert state['secondary'] == 'relay'
    assert state['secondary_model'] is None
    assert result['active_model'] == 'gemini-3.8-flash-high'


@pytest.mark.parametrize('primary,secondary', [
    ('', None), ('   ', None), ('a\x00b', None), ('x' * 201, None),
    (None, None), (['relay'], None), ('ok', 'a\nb'), ('ok', 'x' * 201),
])
def test_save_models_rejects_bad_names(config, primary, secondary):
    seed_state(config.state, primary='relay')
    before = config.state.read_bytes()
    with pytest.raises(ValueError):
        model_pref_panel.save_models(primary, secondary)
    assert config.state.read_bytes() == before


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=None, error=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode()
        self._error = error

    def json(self):
        if self._error:
            raise self._error
        return self._payload


def test_upstream_uses_stored_key_and_returns_ids(config, monkeypatch):
    seed_state(config.state, primary='relay')
    seen = {}

    def fake_get(url, **kwargs):
        seen['url'] = url
        seen.update(kwargs)
        return FakeResponse(payload={'data': [{'id': 'm1'}, {'id': 'm2'}, {'id': 'm1'}]})

    monkeypatch.setattr(model_pref_panel.httpx, 'get', fake_get)
    result = model_pref_panel.fetch_upstream()
    assert result['models'] == ['m1', 'm2']
    assert result['source'] == 'http://old.example/v1'
    assert seen['url'] == 'http://old.example/v1/models'
    assert seen['headers'] == {'Authorization': 'Bearer sk-secret-value'}
    assert seen['follow_redirects'] is False
    assert seen['timeout'] == model_pref_panel.UPSTREAM_TIMEOUT
    assert 'sk-secret-value' not in json.dumps(result)


def test_upstream_accepts_form_base_url_and_key(config, monkeypatch):
    seed_state(config.state, primary='relay')
    seen = {}

    def fake_get(url, **kwargs):
        seen['url'] = url
        seen.update(kwargs)
        return FakeResponse(payload={'data': [{'id': 'model-a'}]})

    monkeypatch.setattr(model_pref_panel.httpx, 'get', fake_get)
    result = model_pref_panel.fetch_upstream('http://other.example/v1/', 'sk-form')
    assert result['source'] == 'http://other.example/v1'
    assert seen['url'] == 'http://other.example/v1/models'
    assert seen['headers'] == {'Authorization': 'Bearer sk-form'}
    assert 'sk-form' not in json.dumps(result)


@pytest.mark.parametrize('response,message', [
    (FakeResponse(status_code=500, payload={}), 'HTTP 500'),
    (FakeResponse(payload={}, error=ValueError('bad json')), '不是 JSON'),
    (FakeResponse(payload={'data': []}), '没有返回可用模型'),
    (FakeResponse(payload={'data': 'nope'}), '格式不正确'),
    (FakeResponse(payload={}, content=b'x' * (model_pref_panel.UPSTREAM_MAX_BYTES + 1)), '过大'),
])
def test_upstream_rejects_bad_responses(config, monkeypatch, response, message):
    seed_state(config.state, primary='relay')
    monkeypatch.setattr(model_pref_panel.httpx, 'get', lambda url, **kwargs: response)
    with pytest.raises(model_pref_panel.UpstreamError) as error:
        model_pref_panel.fetch_upstream()
    assert message in str(error.value)
    assert 'sk-secret-value' not in str(error.value)


def test_upstream_connect_error_never_contains_key(config, monkeypatch):
    seed_state(config.state, primary='relay')

    def fake_get(url, **kwargs):
        raise httpx.ConnectError('boom')

    monkeypatch.setattr(model_pref_panel.httpx, 'get', fake_get)
    with pytest.raises(model_pref_panel.UpstreamError) as error:
        model_pref_panel.fetch_upstream()
    assert '无法连接' in str(error.value)
    assert 'sk-secret-value' not in str(error.value)


def test_upstream_requires_a_key(config, monkeypatch):
    seed_state(config.state, primary='relay')
    config.models.write_text(MODEL_TEXT.replace('  api_key: sk-secret-value\n', ''), encoding='utf-8')
    model_publication.approve_existing(config.models, hashlib.sha256(config.models.read_bytes()).hexdigest())
    with pytest.raises(model_pref_panel.UpstreamError) as error:
        model_pref_panel.fetch_upstream()
    assert 'API Key' in str(error.value)
