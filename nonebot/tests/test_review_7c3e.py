"""Change Reviewer 7c3e 三个阻塞项的原始触发与回归（本机、无凭据、无真实请求）。

1. 旧 pending 任务在发送后/中断转 failed 时，下一次 `enqueue` 的七天清理会不会把它删掉；
2. 首选确认没产出正文（空 choices / 缺 message）时该走次选，已有正文绝不重发；
3. 无持久首选时后台跟随 MuiceBot 原生命令选中的条目，且不误取聊天救援的临时次选。
"""
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
import memory_logic
import model_dispatch as dispatch


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


# ---------------------------------------------------------------- 1. 任务保留

def _legacy_db(path):
    """按候选改动之前的表结构建库：没有 manual_review 等新列。"""
    con = sqlite3.connect(path)
    con.executescript('''
        CREATE TABLE memory_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, event_id TEXT NOT NULL,
            text TEXT, source TEXT NOT NULL, context TEXT NOT NULL, revision INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,event_id));
    ''')
    con.commit()
    con.close()


@pytest.mark.parametrize('recovery', ['interrupted', 'sent_failure'])
def test_sent_or_interrupted_job_survives_seven_day_cleanup(tmp_path, recovery):
    """原始触发：任务 pending 超过七天 → 发送/中断 → 被下一次 enqueue 的清理删掉。"""
    db = tmp_path / 'memory.sqlite3'
    _legacy_db(db)
    job = memory_logic.enqueue(db, 'a', 'keep me', 'private', 'first', [], 0)
    with memory_logic._connect(db) as con:
        con.execute("UPDATE memory_jobs SET created_at=datetime('now','-30 days') WHERE id=?", (job,))
    assert memory_logic.mark_output(db, job)
    if recovery == 'interrupted':
        assert memory_logic.recover_inflight(db) == 1
    else:
        memory_logic.fail_job(db, job, 'TimeoutError')
    with memory_logic._connect(db) as con:
        row = con.execute('SELECT status FROM memory_jobs WHERE id=?', (job,)).fetchone()
    assert row['status'] == 'review', '可能已发送的任务要单独留在待人工判断的状态'

    # 另一个用户正常入队：这次 enqueue 会跑七天清理
    memory_logic.enqueue(db, 'b', 'new', 'private', 'new', [], 0)
    with memory_logic._connect(db) as con:
        rows = con.execute('SELECT id,text,status FROM memory_jobs WHERE id=?', (job,)).fetchall()
    assert [(r['id'], r['text'], r['status']) for r in rows] == [(job, 'keep me', 'review')]
    # 只有用户明确重试才会重新排队
    assert memory_logic.retry_failed(db, 'a') == 1
    assert memory_logic.pending_jobs(db)[0]['text'] == 'keep me'


def test_done_jobs_are_still_cleaned_but_failed_are_not(tmp_path):
    db = tmp_path / 'memory.sqlite3'
    kept = memory_logic.enqueue(db, 'a', 'old failure', 'private', 'kept', [], 0)
    reviewed = memory_logic.enqueue(db, 'a', 'old review', 'private', 'reviewed', [], 0)
    done = memory_logic.enqueue(db, 'a', 'old success', 'private', 'done', [], 0)
    memory_logic.complete_job(db, done)
    with memory_logic._connect(db) as con:
        con.execute("UPDATE memory_jobs SET status='failed' WHERE id=?", (kept,))
        con.execute("UPDATE memory_jobs SET status='review' WHERE id=?", (reviewed,))
        con.execute("UPDATE memory_jobs SET created_at=datetime('now','-30 days')")
    memory_logic.enqueue(db, 'b', 'new', 'private', 'new', [], 0)
    with memory_logic._connect(db) as con:
        ids = {r['id'] for r in con.execute('SELECT id FROM memory_jobs')}
    assert kept in ids, '普通失败任务也不能被按时间清掉，用户还要能重试'
    assert reviewed in ids, '可能已发送、待人工判断的任务必须留下'
    assert done not in ids, '已完成的任务照旧清理'


# ---------------------------------------------------------------- 2. 空响应降级

@pytest.mark.parametrize('first_reply', [
    SimpleNamespace(choices=[]),
    SimpleNamespace(choices=[SimpleNamespace(message=None)]),
    SimpleNamespace(choices=[SimpleNamespace()]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='   '))]),
])
def test_confirmed_no_content_uses_secondary(first_reply):
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        first = AsyncMock(return_value=first_reply)
        second = AsyncMock(return_value=response('ok'))
        def make(cfg):
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first if cfg.model_name == 'p' else second)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=make):
            result = await dispatch.complete(messages=[], timeout=1)
        assert result.choices[0].message.content == 'ok'
        first.assert_awaited_once()
        second.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('result', [
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='already produced'))]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, refusal='no'))]),
    SimpleNamespace(choices=[SimpleNamespace(message=None), SimpleNamespace(message=SimpleNamespace(content='second choice text'))]),
    SimpleNamespace(choices=[], output_text='already produced'),
])
def test_any_nonempty_output_never_retries(result):
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        first = AsyncMock(return_value=result)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client) as factory:
            assert await dispatch.complete(messages=[], timeout=1) is result
        assert factory.call_count == 1, '已有正文就不能重发同一份数据'
    asyncio.run(run())


def test_secondary_also_empty_raises_instead_of_looping():
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        create = AsyncMock(return_value=SimpleNamespace(choices=[]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client):
            with pytest.raises(dispatch.ModelUnavailable):
                await dispatch.complete(messages=[], timeout=1)
        assert create.await_count == 2
    asyncio.run(run())


def test_empty_response_without_failover_raises_and_does_not_loop():
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        create = AsyncMock(return_value=SimpleNamespace(choices=[]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=False), patch.object(dispatch, 'make_client', return_value=client):
            with pytest.raises(dispatch.ModelUnavailable):
                await dispatch.complete(messages=[], timeout=1)
        assert create.await_count == 1
    asyncio.run(run())


def test_unrecognized_shape_is_not_treated_as_empty_response():
    """认不出的形状不能当「没产出」——那会往次选重发同一份图片/记忆。"""
    async def run():
        state = {'primary': 'p', 'secondary': 's'}
        configs = {'p': {'provider': 'openai', 'model_name': 'p'}, 's': {'provider': 'openai', 'model_name': 's'}}
        first = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(other='x'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client) as factory:
            with pytest.raises(dispatch.InvalidModelResponse):
                await dispatch.complete(messages=[], timeout=1)
        assert factory.call_count == 1
    asyncio.run(run())


# ---------------------------------------------------------------- 3. 原生命令选择

def test_native_selected_model_is_used_without_persisted_primary():
    async def run():
        state = {'primary': None, 'primary_model': None, 'secondary': None, 'secondary_model': None}
        configs = {'default': {'provider': 'openai', 'model_name': 'default', 'default': True},
                   'native': {'provider': 'openai', 'model_name': 'native'}}
        create = AsyncMock(return_value=response('ok'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        selection = dict(state, native='native')
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', selection, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'make_client', return_value=client):
            await dispatch.complete(messages=[], timeout=1)
        assert create.await_args.kwargs['model'] == 'native', '无持久首选时后台要跟随原生命令选中的条目'
    asyncio.run(run())


def test_default_entry_is_used_when_no_primary_and_no_native_hint():
    """刚从没设过首选、也没切过模型的旧安装启动：用发布配置确认过的默认条目。"""
    async def run():
        state = {'primary': None, 'primary_model': None, 'secondary': None, 'secondary_model': None}
        configs = {'first': {'provider': 'openai', 'model_name': 'first'},
                   'chosen': {'provider': 'openai', 'model_name': 'chosen', 'default': True}}
        create = AsyncMock(return_value=response('ok'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', state, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'make_client', return_value=client):
            await dispatch.complete(messages=[], timeout=1)
        assert create.await_args.kwargs['model'] == 'chosen'
    asyncio.run(run())


def test_native_and_secondary_use_separate_clients_and_real_names():
    async def run():
        selection = {'primary': None, 'primary_model': None, 'native': 'native',
                     'secondary': 'fallback', 'secondary_model': 'fallback-real'}
        configs = {'native': {'provider': 'openai', 'model_name': 'native-base'},
                   'fallback': {'provider': 'openai', 'model_name': 'fallback-base'}}
        first, second = AsyncMock(side_effect=TimeoutError()), AsyncMock(return_value=response('ok'))
        made = []
        def make(cfg):
            made.append(cfg.model_name)
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=first if cfg.model_name == 'native-base' else second)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', selection, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', side_effect=make):
            assert (await dispatch.complete(messages=[], timeout=1)).choices[0].message.content == 'ok'
        assert made == ['native-base', 'fallback-base']
        assert first.await_args.kwargs['model'] == 'native-base'
        assert second.await_args.kwargs['model'] == 'fallback-real'
    asyncio.run(run())


def test_native_primary_is_not_itself_used_as_fallback():
    """原生条目就是次选时不能重跑同一个模型。"""
    async def run():
        selection = {'primary': None, 'primary_model': None, 'native': 'same',
                     'secondary': 'same', 'secondary_model': None}
        configs = {'same': {'provider': 'openai', 'model_name': 'same'}}
        create = AsyncMock(return_value=SimpleNamespace(choices=[]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
        with patch.object(dispatch, 'read_snapshot', return_value=(b'v', selection, configs)), patch.object(dispatch, 'unchanged', return_value=True), patch.object(dispatch, 'failover_enabled', return_value=True), patch.object(dispatch, 'make_client', return_value=client):
            with pytest.raises(dispatch.ModelUnavailable):
                await dispatch.complete(messages=[], timeout=1)
        assert create.await_count == 1
    asyncio.run(run())


def test_chat_rescue_secondary_is_not_mistaken_for_native_selection():
    """救援把全局临时换成次选：后台读到的仍必须是救场前的条目。"""
    from plugins import model_pref as pref
    from tests.test_model_pref_wiring import FakeConfig, FakeModel, FakeMuice

    primary, fallback = FakeModel(model='native'), FakeModel(model='fallback')
    muice = FakeMuice(primary, name='native')
    with patch.object(pref, '_IN_RESCUE', []), patch.object(pref, '_RESCUE_BASE', None), \
            patch.object(pref, '_NATIVE_SEEN', None), \
            patch.object(pref, 'state', return_value={'primary': None, 'secondary': 'fallback'}), \
            patch.object(pref, '_persist'), patch.object(pref, 'current_name', return_value='native'), \
            patch.object(pref, 'build_pair', return_value=(FakeConfig(name='fallback'), fallback)), \
            patch.object(pref, 'ORIGINAL_ASK', AsyncMock(return_value=SimpleNamespace(text='', succeed=True))), \
            patch.object(pref, 'ORIGINAL_STREAM', None), patch.object(pref, '_flag', return_value=True), \
            patch.object(pref, 'Muice', SimpleNamespace(_instance=muice)):
        holder = {}
        assert pref.native_name() == 'native'
        with patch.object(dispatch, '_selection', wraps=dispatch._selection):
            assert dispatch._selection()['native'] == 'native'
            assert pref._swap_in(muice, 'fallback', holder)
            assert muice.model_config_name == 'fallback', '救场期间全局确实被临时改了'
            assert pref.native_name() == 'native', '临时次选不能被当成主人的选择'
            assert dispatch._selection()['native'] == 'native', '后台这一趟不能被带偏到次选'
            pref._restore(muice, holder)
        assert muice.model is primary and muice.model_config_name == 'native'
        assert pref.native_name() == 'native'


def test_native_switch_via_model_load_is_followed_by_background():
    """MuiceBot 原生 `.model load other` 之后，没有持久首选的后台改为用 other。"""
    from plugins import model_pref as pref
    from tests.test_model_pref_wiring import FakeModel, FakeMuice

    muice = FakeMuice(FakeModel(model='default'), name='default')
    with patch.object(pref, '_IN_RESCUE', []), patch.object(pref, '_RESCUE_BASE', None), \
            patch.object(pref, '_NATIVE_SEEN', None), \
            patch.object(pref, 'state', return_value={'primary': None, 'secondary': None}), \
            patch.object(pref, 'Muice', SimpleNamespace(_instance=muice)):
        assert dispatch._selection()['native'] == 'default'
        # 就是 MuiceBot `handle_model_load` 对 Muice 的调用形态
        muice.change_model_config('other')
        assert dispatch._selection()['native'] == 'other'
        assert muice.model_config_name == 'other'


def test_overlapping_chat_rescues_restore_the_original_model():
    """两趟救场重叠：先退的那趟不能把次选留成全局模型（原评审第 3 项）。"""
    from plugins import model_pref as pref
    from tests.test_model_pref_wiring import FakeConfig, FakeModel, FakeMuice
    import model_pref_logic as logic

    async def run():
        primary = FakeModel(model='native')
        muice = FakeMuice(primary, name='native')
        state = logic.new_state()
        state.update(primary=None, secondary='fallback')
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        counts = {'a': 0, 'b': 0}

        async def ask(self, session, *args):
            counts[session] += 1
            if counts[session] == 1:
                return SimpleNamespace(text='', succeed=True)
            index = 0 if session == 'a' else 1
            entered[index].set()
            await release[index].wait()
            return SimpleNamespace(text='ok', succeed=True)

        build = lambda name: (FakeConfig(name='fallback'), FakeModel(model='fallback'))
        with patch.object(pref, '_IN_RESCUE', []), patch.object(pref, '_RESCUE_BASE', None), \
                patch.object(pref, '_NATIVE_SEEN', None), patch.object(pref, 'state', return_value=state), \
                patch.object(pref, '_persist'), patch.object(pref, 'current_name', return_value='native'), \
                patch.object(pref, 'build_pair', side_effect=build), patch.object(pref, 'ORIGINAL_ASK', ask), \
                patch.object(pref, '_flag', return_value=True):
            a = asyncio.create_task(pref.wrapped_ask(muice, 'a', None))
            await asyncio.wait_for(entered[0].wait(), 1)
            b = asyncio.create_task(pref.wrapped_ask(muice, 'b', None))
            await asyncio.wait_for(entered[1].wait(), 1)
            release[0].set()
            await asyncio.wait_for(a, 1)
            assert muice.model_config_name == 'fallback', '后一趟还在用次选，不能被先退的那趟恢复掉'
            assert pref.native_name() == 'native'
            release[1].set()
            await asyncio.wait_for(b, 1)
            assert not pref._IN_RESCUE
            assert muice.model is primary
            assert muice.model_config_name == 'native'
    asyncio.run(run())


def test_failed_swap_does_not_leave_rescue_marker():
    from plugins import model_pref as pref
    from tests.test_model_pref_wiring import FakeModel, FakeMuice

    muice = FakeMuice(FakeModel(model='native'), name='native')
    with patch.object(pref, '_IN_RESCUE', []), patch.object(pref, '_RESCUE_BASE', None), \
            patch.object(pref, 'state', return_value={'primary': None, 'secondary': 'broken'}), \
            patch.object(pref, 'build_pair', side_effect=RuntimeError('missing entry')):
        holder = {}
        assert pref._swap_in(muice, 'broken', holder) is False
        assert pref._IN_RESCUE == [], '换不上去也不能留下「正在降级」的标记'
        assert muice.model_config_name == 'native'
