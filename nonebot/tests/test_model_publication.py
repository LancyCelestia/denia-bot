import hashlib
from pathlib import Path

import pytest
import model_publication as publication


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_unpublished_and_in_place_edits_are_rejected(tmp_path):
    target = tmp_path / 'models.yml'
    target.write_bytes(b'p: {provider: openai, model_name: old}\n')
    with pytest.raises(publication.UnpublishedConfig):
        publication.read_published(target)
    source = tmp_path / 'prepared.yml'
    payload = b'p: {provider: openai, model_name: new}\n'
    source.write_bytes(payload)
    publication.publish_file(source, target, digest(payload))
    assert publication.read_published(target) == payload
    target.write_bytes(b'p: {provider: openai, model_name: partial}\n')
    with pytest.raises(publication.UnpublishedConfig):
        publication.read_published(target)


def test_wrong_digest_invalid_source_and_same_source_do_not_publish(tmp_path):
    target = tmp_path / 'models.yml'
    original = b'p: {provider: openai, model_name: old}\n'
    source = tmp_path / 'prepared.yml'
    target.write_bytes(original)
    source.write_bytes(b'p: [invalid')
    with pytest.raises(ValueError):
        publication.publish_file(source, target, digest(source.read_bytes()))
    with pytest.raises(ValueError):
        publication.publish_file(source, target, '0' * 64)
    with pytest.raises(ValueError):
        publication.publish_file(target, target, digest(original))
    assert target.read_bytes() == original


def test_manager_watcher_does_not_treat_atomic_move_as_modify(tmp_path):
    from tests._bootstrap import ensure_nonebot
    ensure_nonebot()
    from watchdog.events import FileMovedEvent
    from muicebot.config import ConfigFileHandler
    calls = []
    target = tmp_path / 'models.yml'
    target.write_bytes(b'example')
    handler = ConfigFileHandler(target, lambda: calls.append(True))
    handler.dispatch(FileMovedEvent(str(tmp_path / 'staging'), str(target)))
    assert not calls


def test_interrupted_publication_fails_closed(tmp_path):
    from unittest.mock import patch
    target, source = tmp_path / 'models.yml', tmp_path / 'prepared.yml'
    first = b'p: {provider: openai, model_name: first}\n'
    second = b'p: {provider: openai, model_name: second}\n'
    source.write_bytes(first)
    publication.publish_file(source, target, digest(first))
    source.write_bytes(second)
    real_replace = publication.os.replace
    calls = []
    def interrupt(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError('simulated interruption')
        return real_replace(src, dst)
    with patch.object(publication.os, 'replace', side_effect=interrupt):
        with pytest.raises(OSError):
            publication.publish_file(source, target, digest(second))
    with pytest.raises(publication.UnpublishedConfig):
        publication.read_published(target)


def test_cli_publishes_fake_prepared_configuration(tmp_path):
    import subprocess
    import sys
    source, target = tmp_path / 'prepared.yml', tmp_path / 'models.yml'
    payload = b'p: {provider: openai, model_name: dummy}\n'
    source.write_bytes(payload)
    result = subprocess.run([sys.executable, '-m', 'model_publication', str(source), digest(payload), '--target', str(target)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert publication.read_published(target) == payload


def test_explicit_initialization_approves_existing_file_without_rewriting(tmp_path):
    target = tmp_path / 'models.yml'
    payload = b'p: {provider: openai, model_name: dummy}\n'
    target.write_bytes(payload)
    identity = target.stat().st_ino
    publication.approve_existing(target, digest(payload))
    assert publication.read_published(target) == payload
    assert target.stat().st_ino == identity


def test_explicit_initialization_rejects_wrong_or_changed_hash(tmp_path):
    target = tmp_path / 'models.yml'
    target.write_bytes(b'p: {provider: openai, model_name: old}\n')
    expected = digest(target.read_bytes())
    target.write_bytes(b'p: {provider: openai, model_name: partial}\n')
    with pytest.raises(ValueError):
        publication.approve_existing(target, expected)
    with pytest.raises(publication.UnpublishedConfig):
        publication.read_published(target)


def test_cli_approves_fake_existing_configuration_without_copy(tmp_path):
    import subprocess
    import sys
    target = tmp_path / 'models.yml'
    payload = b'p: {provider: openai, model_name: dummy}\n'
    target.write_bytes(payload)
    result = subprocess.run([sys.executable, '-m', 'model_publication', '--approve-existing', str(target), digest(payload), '--target', str(target)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert publication.read_published(target) == payload
