import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_publication
import persona_settings

MODEL_TEXT = """# 模型配置，注释必须保留
relay:
  provider: openai
  default: true
  template: Denia
  template_mode: system
  model_name: test-model
  api_key: sk-secret-value
  api_host: https://example.invalid
echo-test:
  provider: _echo
  default: false
  template_mode: system
  model_name: echo
"""


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    monkeypatch.setattr(persona_settings, 'get_private', lambda key: '')


def make_templates(tmp_path):
    directory = tmp_path / 'templates'
    directory.mkdir()
    (directory / 'Denia.jinja2').write_text('denia', encoding='utf-8')
    (directory / 'Shorekeeper.jinja2').write_text('shorekeeper', encoding='utf-8')
    (directory / 'notes.txt').write_text('not a template', encoding='utf-8')
    (directory / '.hidden.jinja2').write_text('hidden', encoding='utf-8')
    return directory


def make_models(tmp_path, text=MODEL_TEXT, publish=True):
    directory = tmp_path / 'configs'
    directory.mkdir()
    path = directory / 'models.yml'
    path.write_text(text, encoding='utf-8')
    if publish:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        model_publication.approve_existing(path, digest)
    return path


def write_state(tmp_path, primary=None):
    path = tmp_path / 'data' / 'model_pref.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'primary': primary}), encoding='utf-8')
    return path


def test_list_templates_ignores_non_templates_and_dotfiles(tmp_path):
    templates = make_templates(tmp_path)
    names = [item['name'] for item in persona_settings.list_templates(templates)]
    assert names == ['Denia', 'Shorekeeper']


def test_public_uses_persisted_primary_and_resolves_path(tmp_path, monkeypatch):
    monkeypatch.setattr(persona_settings, 'get_private',
                        lambda key: 'Denia' if key == 'DEFAULT_TEMPLATE' else '')
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = write_state(tmp_path, 'echo-test')

    view = persona_settings.public(models, templates, state)

    assert view['active_model'] == 'echo-test'
    assert view['active_source'] == 'model_pref'
    assert view['default_model'] == 'relay'
    assert view['resolved']['template'] == 'Denia'
    assert view['resolved']['exists'] is True
    assert Path(view['resolved']['path']).is_absolute()
    assert Path(view['resolved']['path']).name == 'Denia.jinja2'
    by_name = {item['name']: item for item in view['models']}
    assert by_name['relay']['template_source'] == 'entry'
    assert by_name['echo-test']['template_source'] == 'default_template'
    assert by_name['echo-test']['active'] is True
    assert view['publication']['published'] is True


def test_public_defaults_to_default_entry_without_persisted_primary(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = tmp_path / 'missing-state.json'

    view = persona_settings.public(models, templates, state)

    assert view['active_model'] == 'relay'
    assert view['active_source'] == 'default'
    assert view['models'][0]['active'] is True


def test_public_reports_missing_template_file(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path, MODEL_TEXT.replace('template: Denia', 'template: Gone'))

    view = persona_settings.public(models, templates, tmp_path / 'no-state.json')

    assert view['resolved']['exists'] is False
    assert view['resolved']['path'] == ''


def test_save_active_scope_updates_active_and_default_and_keeps_bytes(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = write_state(tmp_path, 'echo-test')
    before = models.read_bytes()

    view = persona_settings.save('Shorekeeper', models_path=models, templates_dir=templates,
                                 state_path=state)

    assert view['changed'] == ['echo-test', 'relay']
    assert view['restart_required'] is True
    text = models.read_text(encoding='utf-8')
    assert 'api_key: sk-secret-value' in text
    assert '# 模型配置，注释必须保留' in text
    assert text.count('template: Shorekeeper') == 2
    assert 'template: Denia' not in text
    assert model_publication.read_published(models) == models.read_bytes()
    assert view['resolved']['template'] == 'Shorekeeper'
    assert view['resolved']['path'].endswith('Shorekeeper.jinja2')

    # Only template lines (plus the inserted one) may differ.
    old_lines = before.decode('utf-8').splitlines()
    new_lines = text.splitlines()
    changed = [line for line in new_lines if line not in old_lines]
    assert changed == ['  template: Shorekeeper', '  template: Shorekeeper']


def test_save_all_scope_sets_every_entry(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)

    view = persona_settings.save('Denia', scope='all', models_path=models,
                                 templates_dir=templates, state_path=tmp_path / 'none.json')

    assert view['changed'] == ['relay', 'echo-test']
    assert models.read_text(encoding='utf-8').count('template: Denia') == 2


def test_save_inserts_template_line_when_missing(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = write_state(tmp_path, 'echo-test')

    persona_settings.save('Shorekeeper', models_path=models, templates_dir=templates,
                          state_path=state)

    text = models.read_text(encoding='utf-8')
    assert 'echo-test:\n  template: Shorekeeper\n  provider: _echo' in text


@pytest.mark.parametrize('value', [
    '../evil', '/etc/passwd', 'Denia/../Denia', '.hidden', 'missing', '', '   ', None, ['Denia'],
])
def test_save_rejects_invalid_or_foreign_templates(tmp_path, value):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    before = models.read_bytes()

    with pytest.raises(ValueError):
        persona_settings.save(value, models_path=models, templates_dir=templates,
                              state_path=tmp_path / 'none.json')

    assert models.read_bytes() == before


def test_save_rejects_unpublished_configuration(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    models.write_text(MODEL_TEXT.replace('test-model', 'edited-model'), encoding='utf-8')
    before = models.read_bytes()

    with pytest.raises(persona_settings.Unpublished):
        persona_settings.save('Shorekeeper', models_path=models, templates_dir=templates,
                              state_path=tmp_path / 'none.json')

    assert models.read_bytes() == before


def test_save_rejects_unknown_scope(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)

    with pytest.raises(ValueError):
        persona_settings.save('Denia', scope='everything', models_path=models,
                              templates_dir=templates, state_path=tmp_path / 'none.json')
