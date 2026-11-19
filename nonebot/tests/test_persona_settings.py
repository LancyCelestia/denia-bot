import hashlib
import json
import os
import stat
import subprocess
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


def test_list_templates_excludes_current(tmp_path):
    templates = make_templates(tmp_path)
    (templates / 'current.jinja2').write_text('live body', encoding='utf-8')

    names = [item['name'] for item in persona_settings.list_templates(templates)]

    assert names == ['Denia', 'Shorekeeper']


def test_public_reports_current_metadata(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    current = templates / 'current.jinja2'
    current.write_bytes('当前人格正文'.encode('utf-8'))

    view = persona_settings.public(models, templates, tmp_path / 'none.json')

    meta = view['current']
    assert meta['file'] == 'current.jinja2'
    assert Path(meta['path']) == current.resolve()
    assert meta['exists'] is True
    assert meta['safe'] is True
    assert meta['reason'] == ''
    assert meta['size'] == current.stat().st_size
    assert meta['sha256'] == hashlib.sha256(current.read_bytes()).hexdigest()
    assert meta['modified']
    assert 'current' not in [item['name'] for item in view['templates']]


@pytest.mark.parametrize('content, reason', [
    (b'', '空'),
    (b'\xff\xfe', 'UTF-8'),
    (b'abc\x00def', 'NUL'),
])
def test_public_marks_unusable_current(tmp_path, content, reason):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    (templates / 'current.jinja2').write_bytes(content)

    meta = persona_settings.public(models, templates, tmp_path / 'none.json')['current']

    assert meta['exists'] is True
    assert meta['safe'] is False
    assert reason in meta['reason']


def test_public_reports_missing_current(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)

    meta = persona_settings.public(models, templates, tmp_path / 'none.json')['current']

    assert meta['exists'] is False
    assert meta['safe'] is False
    assert Path(meta['path']) == (templates / 'current.jinja2').resolve()


def test_load_preset_copies_bytes_points_models_at_current(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = write_state(tmp_path, 'echo-test')
    preset_bytes = (templates / 'Shorekeeper.jinja2').read_bytes()

    view = persona_settings.load_preset('Shorekeeper', models_path=models,
                                        templates_dir=templates, state_path=state)

    current = templates / 'current.jinja2'
    assert current.read_bytes() == preset_bytes
    assert (templates / 'Shorekeeper.jinja2').read_bytes() == preset_bytes
    if os.name == 'posix':
        assert stat.S_IMODE(current.stat().st_mode) == 0o600
    text = models.read_text(encoding='utf-8')
    assert text.count('template: current') == 2
    assert 'api_key: sk-secret-value' in text
    assert model_publication.read_published(models) == models.read_bytes()
    assert view['loaded_preset'] == 'Shorekeeper'
    assert view['target_template'] == 'current'
    assert view['models_changed'] == ['echo-test', 'relay']
    assert view['restart_required'] is True
    assert view['resolved']['template'] == 'current'
    assert view['resolved']['exists'] is True
    assert Path(view['resolved']['path']).name == 'current.jinja2'
    assert view['current']['sha256'] == hashlib.sha256(preset_bytes).hexdigest()


def test_load_preset_overwrites_and_skips_reload_when_already_current(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = write_state(tmp_path, 'echo-test')
    persona_settings.load_preset('Shorekeeper', models_path=models,
                                 templates_dir=templates, state_path=state)

    view = persona_settings.load_preset('Denia', models_path=models,
                                        templates_dir=templates, state_path=state)

    assert (templates / 'current.jinja2').read_bytes() == b'denia'
    assert view['models_changed'] == []
    assert view['restart_required'] is False
    assert models.read_bytes().count(b'template: current') == 2


def test_current_body_survives_tracked_preset_changes(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    state = tmp_path / 'none.json'

    persona_settings.load_preset('Denia', models_path=models,
                                 templates_dir=templates, state_path=state)
    current = templates / 'current.jinja2'
    original = current.read_bytes()

    # Simulate an upstream ``git pull`` changing a tracked preset.
    (templates / 'Denia.jinja2').write_text('upstream changed', encoding='utf-8')

    assert current.read_bytes() == original
    assert persona_settings.public(models, templates, state)['current']['safe'] is True

    persona_settings.load_preset('Shorekeeper', models_path=models,
                                 templates_dir=templates, state_path=state)
    assert current.read_bytes() == b'shorekeeper'
    assert (templates / 'Denia.jinja2').read_bytes() == b'upstream changed'


@pytest.mark.parametrize('value', [
    '../evil', '/etc/passwd', 'Denia/../Denia', '.hidden', 'missing', 'current',
    '', '   ', None, ['Denia'],
])
def test_load_preset_rejects_invalid_or_foreign_presets(tmp_path, value):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    before = models.read_bytes()

    with pytest.raises(ValueError):
        persona_settings.load_preset(value, models_path=models, templates_dir=templates,
                                     state_path=tmp_path / 'none.json')

    assert not (templates / 'current.jinja2').exists()
    assert models.read_bytes() == before


def test_load_preset_rejects_unpublished_configuration(tmp_path):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    models.write_text(MODEL_TEXT.replace('test-model', 'edited-model'), encoding='utf-8')
    before = models.read_bytes()

    with pytest.raises(persona_settings.Unpublished):
        persona_settings.load_preset('Shorekeeper', models_path=models,
                                     templates_dir=templates,
                                     state_path=tmp_path / 'none.json')

    assert not (templates / 'current.jinja2').exists()
    assert models.read_bytes() == before


@pytest.mark.parametrize('previous', [None, b'previous body'])
def test_load_preset_restores_current_when_publication_fails(tmp_path, monkeypatch, previous):
    templates = make_templates(tmp_path)
    models = make_models(tmp_path)
    current = templates / 'current.jinja2'
    if previous is not None:
        current.write_bytes(previous)
    before = models.read_bytes()

    def boom(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(persona_settings.model_publication, 'publish_file', boom)

    with pytest.raises(OSError):
        persona_settings.load_preset('Shorekeeper', models_path=models,
                                     templates_dir=templates,
                                     state_path=tmp_path / 'none.json')

    if previous is None:
        assert not current.exists()
    else:
        assert current.read_bytes() == previous
    assert models.read_bytes() == before


def test_current_is_git_ignored_and_presets_remain_tracked():
    repo = Path(persona_settings.__file__).resolve().parents[1]
    try:
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('not a Git checkout')

    ignored = subprocess.run(['git', 'check-ignore', '-q', '--',
                              'nonebot/templates/current.jinja2'],
                             cwd=top, capture_output=True)
    assert ignored.returncode == 0

    for preset in ('nonebot/templates/Denia.jinja2',
                   'nonebot/templates/Shorekeeper.jinja2'):
        tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', '--', preset],
                                 cwd=top, capture_output=True)
        assert tracked.returncode == 0
