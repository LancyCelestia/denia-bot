import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests._bootstrap import ensure_nonebot
ensure_nonebot()
import meme_learning
import meme_logic
import memory_logic
import model_dispatch
from plugins import memory, meme_learn
from tests.test_meme_learn_wiring import picture


def test_memory_presend_failures_remain_pending(tmp_path):
    async def run():
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', side_effect=model_dispatch.SelectionChanged('not published')):
            for _ in range(5):
                await memory._process_job(memory_logic.pending_jobs(db)[0])
        job = memory_logic.pending_jobs(db)[0]
        assert job['attempts'] == 0
        assert memory_logic.job_status(db, 'u').get('failed', 0) == 0
    asyncio.run(run())


def test_memory_interrupt_after_send_is_not_replayed(tmp_path):
    async def run():
        import pytest
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        entered = asyncio.Event()
        async def request(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)), close=AsyncMock())
        state = {'primary': 'p', 'secondary': None}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}}
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'make_client', return_value=client):
            task = asyncio.create_task(memory._process_job(memory_logic.pending_jobs(db)[0]))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not memory_logic.pending_jobs(db)
        assert memory_logic.recover_inflight(db) == 1
        assert memory_logic.job_status(db, 'u')['failed'] == 1
        with memory_logic._connect(db) as connection:
            assert connection.execute('SELECT text FROM memory_jobs WHERE user_id=?', ('u',)).fetchone()[0] == 'example'
    asyncio.run(run())


def test_meme_presend_failure_remains_pending(tmp_path):
    async def run():
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('u', True)
        assert store.enqueue('u', picture()) == 'queued'
        with patch.object(model_dispatch, 'read_snapshot', side_effect=model_dispatch.SelectionChanged('not published')):
            await meme_learn.process_learning_job(store.next_job(), store=store)
        assert store.status('u')['pending'] == 1
        assert store.status('u')['failed'] == 0
    asyncio.run(run())


def test_both_clients_fail_before_send_keep_memory_and_meme_pending(tmp_path):
    async def run():
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        store = meme_learning.Store(meme_logic.Library(tmp_path / 'meme'))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'multimodal': True}, 's': {'provider': 'openai', 'model_name': 's', 'multimodal': True}}
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'failover_enabled', return_value=True), patch.object(model_dispatch, 'make_client', side_effect=RuntimeError('no client')):
            await memory._process_job(memory_logic.pending_jobs(db)[0])
            await meme_learn.process_learning_job(store.next_job(), store=store)
        assert memory_logic.pending_jobs(db)[0]['attempts'] == 0
        assert store.status('u')['pending'] == 1
        assert store.status('u')['failed'] == 0
    asyncio.run(run())


def test_meme_interrupted_after_send_never_automatically_requeues(tmp_path):
    async def run():
        import pytest
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        entered = asyncio.Event()
        async def request(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)), close=AsyncMock())
        state = {'primary': 'p', 'secondary': None}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'multimodal': True}}
        with patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'make_client', return_value=client):
            task = asyncio.create_task(meme_learn.process_learning_job(store.next_job(), store=store))
            await asyncio.wait_for(entered.wait(), 1)
            assert store.next_job() is None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert store.next_job() is None
        assert store.recover_inflight() == 1
        assert store.status('u')['failed'] == 1
        assert store.next_job() is None
        assert store.retry('u') == 1
        assert store.next_job()['content'] == picture()
    asyncio.run(run())


def test_meme_success_after_pre_send_reservation(tmp_path):
    async def run():
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"category":"test","description":"ok"}'))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=result))), close=AsyncMock())
        state = {'primary': 'p', 'secondary': None}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'multimodal': True}}
        with patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'make_client', return_value=client), patch.object(meme_learning, 'embed', new_callable=AsyncMock, return_value=[1.0]):
            await meme_learn.process_learning_job(store.next_job(), store=store)
        assert store.status('u')['done'] == 1
        assert store.next_job() is None
    asyncio.run(run())


def test_memory_transient_backfill_before_send_does_not_exhaust_job(tmp_path):
    async def run():
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock, side_effect=TimeoutError()):
            for _ in range(5):
                await memory._process_job(memory_logic.pending_jobs(db)[0])
        assert memory_logic.pending_jobs(db)[0]['attempts'] == 0
    asyncio.run(run())


def test_missing_client_method_does_not_mark_memory_sent(tmp_path):
    async def run():
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        bad = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()), close=AsyncMock())
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'failover_enabled', return_value=True), patch.object(model_dispatch, 'make_client', return_value=bad):
            await memory._process_job(memory_logic.pending_jobs(db)[0])
        assert memory_logic.pending_jobs(db)[0]['attempts'] == 0
    asyncio.run(run())


def test_legacy_default_missing_marker_then_explicit_approval_recovers(tmp_path):
    import hashlib
    import muicebot.config as config_module
    import model_publication
    async def run():
        target = tmp_path / 'models.yml'
        content = b'p:\n  provider: openai\n  model_name: p\n  multimodal: true\n  default: true\n'
        target.write_bytes(content)
        manager = object.__new__(config_module.ModelConfigManager)
        manager.configs, manager.default_config = {}, None
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        store = meme_learning.Store(meme_logic.Library(tmp_path / 'meme'))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        async def request(**kwargs):
            user = kwargs['messages'][-1]['content']
            text = '{"category":"test","description":"ok"}' if isinstance(user, list) else '{"memories":[]}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=request))), close=AsyncMock())
        selected = {'primary': None, 'primary_model': None, 'secondary': None, 'secondary_model': None}
        with patch.object(config_module, 'MODELS_CONFIG_PATH', target), patch.object(config_module, 'get_model_config_manager', return_value=manager), patch.object(model_dispatch, '_selection', return_value=selected), patch.object(model_dispatch, 'make_client', return_value=client), patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(meme_learning, 'embed', new_callable=AsyncMock, return_value=[1.0]):
            manager._load_configs()
            await memory._process_job(memory_logic.pending_jobs(db)[0])
            await meme_learn.process_learning_job(store.next_job(), store=store)
            assert memory_logic.pending_jobs(db)[0]['attempts'] == 0
            assert store.status('u')['pending'] == 1
            client.chat.completions.create.assert_not_awaited()
            model_publication.approve_existing(target, hashlib.sha256(content).hexdigest())
            await memory._process_job(memory_logic.pending_jobs(db)[0])
            await meme_learn.process_learning_job(store.next_job(), store=store)
            assert not memory_logic.pending_jobs(db)
            assert store.status('u')['done'] == 1
            assert [x.kwargs['model'] for x in client.chat.completions.create.await_args_list] == ['p', 'p']
    asyncio.run(run())


def test_in_place_edit_pauses_without_losing_queued_jobs(tmp_path):
    import hashlib
    import muicebot.config as config_module
    import model_publication
    async def run():
        target = tmp_path / 'models.yml'
        initial = b'p:\n  provider: openai\n  model_name: old\n  default: true\n'
        edited = b'p:\n  provider: openai\n  model_name: new\n  default: true\n'
        target.write_bytes(initial)
        model_publication.approve_existing(target, hashlib.sha256(initial).hexdigest())
        target.write_bytes(edited)
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        store = meme_learning.Store(meme_logic.Library(tmp_path / 'meme'))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        with patch.object(config_module, 'MODELS_CONFIG_PATH', target), patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'make_client') as make:
            await memory._process_job(memory_logic.pending_jobs(db)[0])
            await meme_learn.process_learning_job(store.next_job(), store=store)
            make.assert_not_called()
        assert memory_logic.pending_jobs(db)[0]['attempts'] == 0
        assert store.status('u')['pending'] == 1
    asyncio.run(run())


def test_meme_transient_store_error_before_send_stays_pending(tmp_path):
    import sqlite3
    async def run():
        store = meme_learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        with patch.object(store.library, 'categories', side_effect=sqlite3.OperationalError('busy')):
            for _ in range(3):
                await meme_learn.process_learning_job(store.next_job(), store=store)
        assert store.status('u')['pending'] == 1
        assert store.status('u')['failed'] == 0
    asyncio.run(run())


def test_in_place_edit_needs_new_approval_and_manager_reload(tmp_path):
    import hashlib
    import pytest
    import muicebot.config as config_module
    import model_publication
    async def run():
        target = tmp_path / 'models.yml'
        old = b'p:\n  provider: openai\n  model_name: old\n  default: true\n'
        new = b'p:\n  provider: openai\n  model_name: new\n  default: true\n'
        target.write_bytes(old)
        model_publication.approve_existing(target, hashlib.sha256(old).hexdigest())
        manager = object.__new__(config_module.ModelConfigManager)
        manager.configs, manager.default_config = {}, None
        selected = {'primary': None, 'primary_model': None, 'secondary': None, 'secondary_model': None}
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(config_module, 'MODELS_CONFIG_PATH', target), patch.object(config_module, 'get_model_config_manager', return_value=manager), patch.object(model_dispatch, '_selection', return_value=selected), patch.object(model_dispatch, 'make_client', return_value=client):
            manager._load_configs()
            target.write_bytes(new)
            with pytest.raises(model_dispatch.PreSendUnavailable):
                await model_dispatch.complete(messages=[], timeout=1)
            model_publication.approve_existing(target, hashlib.sha256(new).hexdigest())
            with pytest.raises(model_dispatch.PreSendUnavailable):
                await model_dispatch.complete(messages=[], timeout=1)
            manager._load_configs()
            await model_dispatch.complete(messages=[], timeout=1)
        assert create.await_count == 1
        assert create.await_args.kwargs['model'] == 'new'
    asyncio.run(run())


def test_sent_failure_is_not_automatically_replayed(tmp_path):
    async def run():
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        store = meme_learning.Store(meme_logic.Library(tmp_path / 'meme'))
        store.set_enabled('u', True)
        store.enqueue('u', picture())
        create = AsyncMock(side_effect=TimeoutError())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        state = {'primary': 'p', 'secondary': None}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'multimodal': True}}
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'make_client', return_value=client):
            await memory._process_job(memory_logic.pending_jobs(db)[0])
            await meme_learn.process_learning_job(store.next_job(), store=store)
        assert create.await_count == 2
        assert not memory_logic.pending_jobs(db)
        assert memory_logic.job_status(db, 'u')['failed'] == 1
        assert store.next_job() is None
        assert store.status('u')['failed'] == 1
    asyncio.run(run())


def test_same_dispatch_may_send_to_configured_secondary_once():
    async def run():
        first, second = AsyncMock(side_effect=TimeoutError()), AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))]))
        calls = []
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        def make(cfg):
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first if cfg.model_name == 'p' else second)), close=AsyncMock())
        with patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'failover_enabled', return_value=True), patch.object(model_dispatch, 'make_client', side_effect=make):
            await model_dispatch.complete(messages=[], timeout=1, before_send=lambda: calls.append('marked') or True)
        assert first.await_count == 1 and second.await_count == 1 and calls == ['marked']
    asyncio.run(run())


def test_memory_inflight_is_not_manual_retryable_until_recovered(tmp_path):
    db = tmp_path / 'memory.sqlite3'
    job_id = memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
    assert memory_logic.mark_output(db, job_id)
    assert memory_logic.retry_failed(db, 'u') == 0
    assert not memory_logic.pending_jobs(db)
    assert memory_logic.job_status(db, 'u')['pending'] == 1
    assert memory_logic.recover_inflight(db) == 1
    assert memory_logic.job_status(db, 'u')['failed'] == 1
    assert memory_logic.retry_failed(db, 'u') == 1


def test_interruption_after_durable_marker_before_client_call_needs_review(tmp_path):
    class SimulatedCrash(BaseException):
        pass
    async def run():
        import pytest
        db = tmp_path / 'memory.sqlite3'
        memory_logic.enqueue(db, 'u', 'example', 'private', 'event', [], 0)
        def interrupted(**kwargs):
            raise SimulatedCrash()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=interrupted)), close=AsyncMock())
        state = {'primary': 'p', 'secondary': None}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}}
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(model_dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(model_dispatch, 'unchanged', return_value=True), patch.object(model_dispatch, 'make_client', return_value=client):
            with pytest.raises(SimulatedCrash):
                await memory._process_job(memory_logic.pending_jobs(db)[0])
        assert memory_logic.recover_inflight(db) == 1
        assert memory_logic.job_status(db, 'u')['failed'] == 1
        with memory_logic._connect(db) as connection:
            assert connection.execute('SELECT text FROM memory_jobs WHERE user_id=?', ('u',)).fetchone()[0] == 'example'
    asyncio.run(run())
