import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from plugins import meme_learn, memory
from tests.test_meme_learn_wiring import picture


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_vision_uses_scheduler_and_standard_png():
    async def run():
        result = reply('{"category":"test","category_description":"test","description":"ok"}')
        with patch.object(meme_learn.model_dispatch, 'complete', new_callable=AsyncMock, return_value=result) as complete:
            assert (await meme_learn.describe_image(picture(), []))['description'] == 'ok'
            args = complete.await_args.kwargs
            assert args['require_vision'] is True
            assert args['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    asyncio.run(run())


def test_memory_extraction_uses_scheduler():
    async def run():
        result = reply('{"memories":[]}')
        with patch.object(memory.model_dispatch, 'complete', new_callable=AsyncMock, return_value=result) as complete, patch.object(memory, '_backfill', new_callable=AsyncMock):
            await memory._extract('example', 'example text', 'private', 0, context=[])
            assert complete.await_args.kwargs['timeout'] == 45
            assert complete.await_args.kwargs['extra_headers']['x-opencode-session'].startswith('memory-')
    asyncio.run(run())


def test_memory_postprocess_failure_is_not_automatically_requeued(tmp_path):
    async def run():
        import memory_logic as logic
        db = tmp_path / 'memory.sqlite3'
        job_id = logic.enqueue(db, '42', 'example', 'private', 'event', [], logic.revision(db, '42'))
        job = logic.pending_jobs(db)[0]
        generated = reply('{"memories":["example"]}')
        async def generated_request(**kwargs):
            assert kwargs['before_send']()
            return generated
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory.model_dispatch, 'complete', side_effect=generated_request), patch.object(memory, '_backfill', new_callable=AsyncMock), patch.object(memory, '_embed', new_callable=AsyncMock, side_effect=TimeoutError):
            await memory._process_job(job)
        assert not logic.pending_jobs(db)
    asyncio.run(run())


def test_memory_cancelled_in_flight_does_not_auto_replay(tmp_path):
    async def run():
        import pytest
        import memory_logic as logic
        db = tmp_path / 'memory.sqlite3'
        logic.enqueue(db, '42', 'example', 'private', 'event', [], logic.revision(db, '42'))
        job = logic.pending_jobs(db)[0]
        entered = asyncio.Event()
        async def block(**kwargs):
            assert kwargs['before_send']()
            entered.set()
            await asyncio.Event().wait()
        with patch.object(memory.memory, 'DEFAULT_PATH', db), patch.object(memory.model_dispatch, 'complete', side_effect=block), patch.object(memory, '_backfill', new_callable=AsyncMock):
            task = asyncio.create_task(memory._process_job(job))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not logic.pending_jobs(db)
        assert logic.recover_inflight(db) == 1
        assert logic.job_status(db, '42')['failed'] == 1
    asyncio.run(run())


def test_vision_and_memory_fallback_use_configured_clients():
    async def run():
        import model_dispatch as dispatcher
        state = {'primary': 'p', 'secondary': 's', 'primary_model': 'p-real', 'secondary_model': 's-real'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p', 'multimodal': True}, 's': {'provider': 'openai', 'model_name': 's', 'multimodal': True}}
        primary = AsyncMock(side_effect=TimeoutError())
        secondary = AsyncMock(side_effect=[reply('{"category":"x","description":"ok"}'), reply('{"memories":[]}')])
        def make(cfg):
            request = primary if cfg.model_name == 'p' else secondary
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)), close=AsyncMock())
        with patch.object(dispatcher, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatcher, 'unchanged', return_value=True), patch.object(dispatcher, 'failover_enabled', return_value=True), patch.object(dispatcher, 'make_client', side_effect=make), patch.object(memory, '_backfill', new_callable=AsyncMock):
            await meme_learn.describe_image(picture(), [])
            await memory._extract('fake', 'fake memory', 'private', 0)
        assert [c.kwargs['model'] for c in primary.await_args_list] == ['p-real', 'p-real']
        assert [c.kwargs['model'] for c in secondary.await_args_list] == ['s-real', 's-real']
        image = secondary.await_args_list[0].kwargs['messages'][0]['content'][1]['image_url']['url']
        assert image.startswith('data:image/png;base64,')
        assert secondary.await_args_list[1].kwargs['extra_headers']['x-opencode-session'].startswith('memory-')
    asyncio.run(run())
