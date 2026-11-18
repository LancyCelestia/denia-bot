import asyncio
import hashlib
import model_publication as publication
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
import model_dispatch as dispatch


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_primary_success_does_not_send_to_secondary():
    async def run():
        first, second = AsyncMock(return_value=response('ok')), AsyncMock(return_value=response('wrong'))
        clients = {'old': first, 's': second}
        state = {'primary': 'primary', 'secondary': 'secondary', 'primary_model': 'override', 'secondary_model': None}
        configs = {'primary': {'provider': 'openai', 'model_name': 'old', 'multimodal': True}, 'secondary': {'provider': 'openai', 'model_name': 's', 'multimodal': True}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v1', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'make_client', side_effect=lambda cfg: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=clients[cfg.model_name])), close=AsyncMock())):
            result = await dispatch.complete(messages=[], timeout=1, require_vision=True)
        assert result.choices[0].message.content == 'ok'
        assert first.await_args.kwargs['model'] == 'override'
        second.assert_not_awaited()
    asyncio.run(run())


def test_failed_primary_uses_secondary_client_and_name():
    async def run():
        first, second = AsyncMock(side_effect=TimeoutError()), AsyncMock(return_value=response('ok'))
        state = {'primary': 'primary', 'secondary': 'secondary', 'primary_model': 'first-real', 'secondary_model': 'second-real'}
        configs = {'primary': {'provider': 'openai', 'model_name': 'primary'}, 'secondary': {'provider': 'openai', 'model_name': 'secondary'}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v1', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=lambda cfg: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first if cfg.model_name == 'primary' else second)), close=AsyncMock())):
            result = await dispatch.complete(messages=[], timeout=1)
        assert result.choices[0].message.content == 'ok'
        assert first.await_args.kwargs['model'] == 'first-real'
        assert second.await_args.kwargs['model'] == 'second-real'
    asyncio.run(run())


def test_change_during_primary_failure_rejects_secondary():
    async def run():
        first = AsyncMock(side_effect=TimeoutError())
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'old', state, configs)), patch.object(dispatch, 'unchanged', side_effect=[True, True, False]), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first)), close=AsyncMock())) as make:
            with pytest.raises(dispatch.SelectionChanged):
                await dispatch.complete(messages=[], timeout=1)
        assert make.call_count == 1
    asyncio.run(run())


def test_fallback_disabled_and_nonempty_output_never_retried():
    async def run():
        first = AsyncMock(side_effect=TimeoutError())
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=False), patch.object(dispatch, 'make_client', return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first)), close=AsyncMock())) as make:
            with pytest.raises(TimeoutError):
                await dispatch.complete(messages=[], timeout=1)
            assert make.call_count == 1
            first.side_effect = None
            first.return_value = response('not-json-but-output')
            assert (await dispatch.complete(messages=[], timeout=1)).choices[0].message.content == 'not-json-but-output'
            assert make.call_count == 2
    asyncio.run(run())


def test_actual_file_update_invalidates_snapshot(tmp_path):
    import muicebot.config as cm
    from muicebot.llm import ModelConfig
    config, source = tmp_path / 'models.yml', tmp_path / 'source.yml'
    first = b'p:\n  provider: openai\n  model_name: first\n'
    second = b'p:\n  provider: openai\n  model_name: second\n'
    source.write_bytes(first)
    publication.publish_file(source, config, hashlib.sha256(first).hexdigest())
    state = {'primary': 'p', 'primary_model': None, 'secondary': None, 'secondary_model': None}
    selected_config = ModelConfig(provider='openai', model_name='first')
    manager = SimpleNamespace(configs={'p': selected_config}, default_config=selected_config)
    with patch.object(cm, 'MODELS_CONFIG_PATH', config), patch.object(cm, 'get_model_config_manager', return_value=manager), patch.object(dispatch, '_selection', return_value=state):
        raw, selected, entries = dispatch.read_snapshot()
        assert entries['p']['model_name'] == 'first'
        assert dispatch.unchanged(raw, selected)
        source.write_bytes(second)
        publication.publish_file(source, config, hashlib.sha256(second).hexdigest())
        assert not dispatch.unchanged(raw, selected)
        with pytest.raises(dispatch.SelectionChanged):
            dispatch.read_snapshot()
        selected_config = ModelConfig(provider='openai', model_name='second')
        manager.configs = {'p': selected_config}
        manager.default_config = selected_config
        assert dispatch.read_snapshot()[2]['p']['model_name'] == 'second'


def test_snapshot_rejects_partially_reloaded_manager(tmp_path):
    import muicebot.config as cm
    from muicebot.llm import ModelConfig
    config, source = tmp_path / 'models.yml', tmp_path / 'source.yml'
    content = b'p:\n  provider: openai\n  model_name: p\ns:\n  provider: openai\n  model_name: s\n'
    source.write_bytes(content)
    publication.publish_file(source, config, hashlib.sha256(content).hexdigest())
    selected_config = ModelConfig(provider='openai', model_name='p')
    manager = SimpleNamespace(configs={'p': selected_config}, default_config=selected_config)
    with patch.object(cm, 'MODELS_CONFIG_PATH', config), patch.object(cm, 'get_model_config_manager', return_value=manager), patch.object(dispatch, '_selection', return_value={'primary': 'p'}):
        with pytest.raises(dispatch.SelectionChanged):
            dispatch.read_snapshot()


def test_malformed_response_never_calls_secondary():
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        first = AsyncMock(return_value=SimpleNamespace(choices=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client) as make:
            with pytest.raises(dispatch.InvalidModelResponse):
                await dispatch.complete(messages=[], timeout=1)
        assert make.call_count == 1
    asyncio.run(run())


def test_same_entry_different_real_names_and_isolated_from_chat_rescue():
    async def run():
        from plugins import model_pref
        from muicebot.muice import Muice
        create = AsyncMock(side_effect=[TimeoutError(), response('ok')])
        clients = []
        def make(cfg):
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
            clients.append(client)
            return client
        state = {'primary': 'relay', 'secondary': 'relay', 'primary_model': 'first', 'secondary_model': 'second'}
        configs = {'relay': {'provider': 'openai', 'model_name': 'base'}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=make), patch.object(model_pref, '_IN_RESCUE', [object()]), patch.object(Muice, 'get_instance', side_effect=AssertionError('global model must not be accessed')):
            await dispatch.complete(messages=[], timeout=1)
        assert [c.kwargs['model'] for c in create.await_args_list] == ['first', 'second']
        assert len(clients) == 2 and clients[0] is not clients[1]
        assert all(c.close.await_count == 1 for c in clients)
    asyncio.run(run())


def test_both_fail_and_client_construction_error_falls_back():
    async def run():
        first_failure, second = ValueError('fake private payload'), AsyncMock(side_effect=TimeoutError())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=second)), close=AsyncMock())
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=[first_failure, client]):
            with pytest.raises(TimeoutError):
                await dispatch.complete(messages=[], timeout=1)
        second.assert_awaited_once()
        client.close.assert_awaited_once()
    asyncio.run(run())


def test_live_partial_config_must_not_send_to_stale_endpoint(tmp_path):
    import threading
    import muicebot.config as cm
    async def run():
        path = tmp_path / 'models.yml'
        first = 'p:\n  provider: openai\n  model_name: dummy\n  api_key: dummy\n  api_host: https://old.invalid/v1\n'
        remainder = '  api_host: https://example.invalid/v1\n'
        prepared = tmp_path / 'prepared.yml'
        initial = (first + remainder).encode('utf8')
        prepared.write_bytes(initial)
        publication.publish_file(prepared, path, hashlib.sha256(initial).hexdigest())
        written, resume = threading.Event(), threading.Event()
        def writer():
            with path.open('w', encoding='utf8') as out:
                out.write(first)
                out.flush()
                written.set()
                assert resume.wait(5)
                out.write(remainder)
        thread = threading.Thread(target=writer)
        thread.start()
        assert written.wait(5)
        manager = object.__new__(cm.ModelConfigManager)
        manager.configs = {}
        manager.default_config = None
        state = {'primary': 'p', 'primary_model': None, 'secondary': None, 'secondary_model': None}
        called = AsyncMock(return_value=response('ok'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=called)), close=AsyncMock())
        try:
            with patch.object(cm, 'MODELS_CONFIG_PATH', path), patch.object(cm, 'get_model_config_manager', return_value=manager), patch.object(dispatch, '_selection', return_value=state):
                manager._load_configs()
                with patch.object(dispatch, 'make_client', return_value=client):
                    with pytest.raises(dispatch.SelectionChanged):
                        await dispatch.complete(messages=[], timeout=1)
        finally:
            resume.set()
            thread.join(5)
        called.assert_not_awaited()
    asyncio.run(run())


def test_missing_primary_uses_published_default_and_secondary():
    async def run():
        state = {'primary': None, 'secondary': 's', 'secondary_model': 'fallback-real'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'default': True}, 's': {'provider': 'openai', 'model_name': 's'}}
        first, second = AsyncMock(side_effect=TimeoutError()), AsyncMock(return_value=response('ok'))
        def make(cfg):
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first if cfg.model_name == 'p' else second)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=make):
            await dispatch.complete(messages=[], timeout=1)
        assert first.await_args.kwargs['model'] == 'p'
        assert second.await_args.kwargs['model'] == 'fallback-real'
    asyncio.run(run())


def test_unsupported_primary_can_fail_over_to_supported_secondary():
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'gemini', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        create = AsyncMock(return_value=response('ok'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client) as make:
            await dispatch.complete(messages=[], timeout=1)
        assert make.call_count == 1
        assert create.await_args.kwargs['model'] == 's'
    asyncio.run(run())


def test_real_manager_published_default_and_reload(tmp_path):
    import muicebot.config as cm
    async def run():
        target, source = tmp_path / 'models.yml', tmp_path / 'source.yml'
        initial = b'old:\n  provider: openai\n  model_name: old\n  default: true\n'
        updated = b'new:\n  provider: openai\n  model_name: new\n  default: true\n'
        source.write_bytes(initial)
        publication.publish_file(source, target, hashlib.sha256(initial).hexdigest())
        manager = object.__new__(cm.ModelConfigManager)
        manager.configs = {}
        manager.default_config = None
        state = {'primary': None, 'secondary': None, 'primary_model': None, 'secondary_model': None}
        create = AsyncMock(return_value=response('ok'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(cm, 'MODELS_CONFIG_PATH', target), patch.object(cm, 'get_model_config_manager', return_value=manager), patch.object(dispatch, '_selection', return_value=state), patch.object(dispatch, 'make_client', return_value=client):
            manager._load_configs()
            await dispatch.complete(messages=[], timeout=1)
            assert create.await_args.kwargs['model'] == 'old'
            source.write_bytes(updated)
            publication.publish_file(source, target, hashlib.sha256(updated).hexdigest())
            with pytest.raises(dispatch.SelectionChanged):
                await dispatch.complete(messages=[], timeout=1)
            assert create.await_count == 1
            manager._load_configs()
            await dispatch.complete(messages=[], timeout=1)
            assert create.await_args.kwargs['model'] == 'new'
    asyncio.run(run())


def test_real_manager_stale_default_fails_until_restart(tmp_path):
    import muicebot.config as cm
    target, source = tmp_path / 'models.yml', tmp_path / 'source.yml'
    old = b'old:\n  provider: openai\n  model_name: old\n  default: true\n'
    new = b'new:\n  provider: openai\n  model_name: new\n'
    manager = object.__new__(cm.ModelConfigManager)
    manager.configs = {}
    manager.default_config = None
    with patch.object(cm, 'MODELS_CONFIG_PATH', target), patch.object(cm, 'get_model_config_manager', return_value=manager), patch.object(dispatch, '_selection', return_value={'primary': None}):
        source.write_bytes(old)
        publication.publish_file(source, target, hashlib.sha256(old).hexdigest())
        manager._load_configs()
        source.write_bytes(new)
        publication.publish_file(source, target, hashlib.sha256(new).hexdigest())
        manager._load_configs()
        with pytest.raises(dispatch.SelectionChanged):
            dispatch.read_snapshot()
        manager.default_config = None  # fresh manager on restart
        manager._load_configs()
        assert dispatch._default_name(dispatch.read_snapshot()[2]) == 'new'
