import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot_identity


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setattr(bot_identity, 'get_private', lambda key: '')
    monkeypatch.delenv('BOT_NAME', raising=False)
    monkeypatch.delenv('QQBOT_NAME', raising=False)


def test_default_and_roundtrip(tmp_path):
    target = tmp_path / 'bot-name.json'
    assert bot_identity.resolve(target) == (bot_identity.DEFAULT_NAME, 'default')

    saved = bot_identity.save('  泡泡机  ', target)
    assert saved == {'name': '泡泡机', 'source': 'file', 'default': bot_identity.DEFAULT_NAME}
    assert bot_identity.display_name(target) == '泡泡机'
    assert target.read_text(encoding='utf-8') == '{"name": "泡泡机"}'

    reset = bot_identity.reset(target)
    assert reset['name'] == bot_identity.DEFAULT_NAME
    assert reset['source'] == 'default'
    assert not target.exists()


def test_environment_fallback_and_invalid_env(monkeypatch, tmp_path):
    values = {'BOT_NAME': '环境名'}
    monkeypatch.setattr(bot_identity, 'get_private', lambda key: values.get(key, ''))
    assert bot_identity.resolve(tmp_path / 'missing.json') == ('环境名', 'env')

    values['BOT_NAME'] = '   '
    assert bot_identity.resolve(tmp_path / 'missing.json') == (bot_identity.DEFAULT_NAME, 'default')


def test_validation_rejects_empty_control_and_long_names(tmp_path):
    target = tmp_path / 'bot-name.json'
    for value in ('', '   ', 'a\nb', 'x' * 41):
        with pytest.raises(ValueError):
            bot_identity.save(value, target)
    assert not target.exists()


def test_corrupt_file_falls_back_to_default(tmp_path):
    target = tmp_path / 'bot-name.json'
    target.write_text('{broken', encoding='utf-8')
    assert bot_identity.resolve(target) == (bot_identity.DEFAULT_NAME, 'default')
