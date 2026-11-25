import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import nonebot
try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelRequest
from muicebot.models import Message as ChatMessage
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message as OBMessage, MessageSegment
from plugins import memory as plugin
import plugins.prompt_guard as prompt_guard

SELF = 10000001
GRP = 123456789
GKEY = f"qq_OneBot V11_{SELF}_{GRP}"
STAMP_TS = 1790493646


def _stamp(ts):
    return datetime.fromtimestamp(ts).strftime('%Y.%m.%d %H:%M:%S')


def _group_event(text, message_id=1):
    message = OBMessage()
    message += MessageSegment.text(text)
    return GroupMessageEvent(
        time=STAMP_TS, self_id=SELF, post_type='message', message_type='group',
        sub_type='normal', user_id=1, group_id=GRP, message_id=message_id,
        message=message, raw_message=str(message), font=0,
        sender={'card': '群名片', 'nickname': '昵称', 'role': 'member', 'title': ''},
        to_me=True,
    )


@contextmanager
def _memory_env():
    old_path, old_embed = plugin.memory.DEFAULT_PATH, plugin._embed
    with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
        plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
        try:
            yield plugin.memory.DEFAULT_PATH
        finally:
            plugin.memory.DEFAULT_PATH = old_path
            plugin._embed = old_embed


class EventStub:
    def __init__(self, uid, kind, *, group_id=None, sender=None, time=None):
        self.uid, self.message_type = uid, kind
        if group_id is not None:
            self.group_id = group_id
        if sender is not None:
            self.sender = sender
        if time is not None:
            self.time = time

    def get_user_id(self):
        return self.uid

    def get_plaintext(self):
        return '我喜欢喝咖啡'

    self_id = 10000001
    message_id = 123456


class FakeCompletions:
    def __init__(self, extraction='{"memories":["用户喜欢喝咖啡"]}', relations=None):
        self.extraction = extraction
        self.relations = relations

    async def create(self, **kwargs):
        system = ((kwargs.get('messages') or [{}])[0] or {}).get('content', '')
        if '记忆关系分类器' in system:
            if self.relations is None:
                raise RuntimeError('relation model unavailable')
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.relations))])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.extraction))])


class MemoryWiringTest(unittest.TestCase):
    def test_background_extraction_and_cross_scope_injection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                async def fake_embed(_text):
                    return [1.0, 0.0]
                plugin._embed = fake_embed
                version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '42')
                with patch.object(plugin.model_dispatch, 'complete', side_effect=FakeCompletions().create):
                    asyncio.run(plugin._extract('42', '我喜欢咖啡', 'private', version))
                req = ModelRequest(prompt='你好', system='人格')
                asyncio.run(plugin.inject(req, EventStub('42', 'group')))
                self.assertIn('用户喜欢喝咖啡', req.system)
                self.assertIn('不要主动', req.system)
                other = ModelRequest(prompt='你好', system='人格')
                asyncio.run(plugin.inject(other, EventStub('43', 'group')))
                self.assertEqual(other.system, '人格')
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_durable_job_retries_and_then_completes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                event = EventStub('42', 'private')
                message = SimpleNamespace(respond='收到')
                asyncio.run(plugin.inject(ModelRequest(prompt='我喜欢喝咖啡'), event))
                asyncio.run(plugin.collect(message, event))
                job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
                async def good_embed(_text):
                    return [1.0, 0.0]
                plugin._embed = good_embed
                with patch.object(plugin, '_backfill', side_effect=[TimeoutError('temporary'), None]), patch.object(plugin.model_dispatch, 'complete', side_effect=FakeCompletions().create):
                    asyncio.run(plugin._process_job(job))
                    job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
                    self.assertEqual(job['attempts'], 0)
                    asyncio.run(plugin._process_job(job))
                self.assertEqual(plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH), [])
                self.assertEqual(len(plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '42')), 1)
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_origin_participants_and_names_flow_into_observation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                async def fake_embed(_text):
                    return [1.0, 0.0]
                plugin._embed = fake_embed
                event = EventStub('1', 'group', group_id='123',
                                  sender=SimpleNamespace(card='群名片', nickname='昵称'),
                                  time=datetime(2026, 11, 24, 20, 30, 0))
                stamp = _stamp(int(datetime(2026, 11, 24, 20, 30, 0).timestamp()))
                request = ModelRequest(prompt='你好', history=[
                    ChatMessage(time=stamp, userid='1', groupid='123', message='<昵称> 我先说'),
                    ChatMessage(time=stamp, userid='2', groupid='123', message='<小明> 我也在'),
                ])
                asyncio.run(plugin.inject(request, event))
                asyncio.run(plugin.collect(SimpleNamespace(respond='收到'), event))
                job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
                self.assertEqual(job['origin_group_id'], '123')
                self.assertEqual(int(job['origin_ts']),
                                 int(datetime(2026, 11, 24, 20, 30, 0).timestamp()))
                self.assertEqual(json.loads(job['origin_participants']), ['2'])
                with plugin.memory._connect(plugin.memory.DEFAULT_PATH) as con:
                    names = {(row['user_id'], row['group_id']): row['display_name']
                             for row in con.execute(
                                 'SELECT user_id,group_id,display_name FROM memory_people')}
                self.assertEqual(names[('2', '123')], '小明')
                self.assertEqual(names[('1', '123')], '群名片')
                with patch.object(plugin.model_dispatch, 'complete',
                                  side_effect=FakeCompletions().create):
                    asyncio.run(plugin._process_job(job))
                described = plugin.memory.describe_user(plugin.memory.DEFAULT_PATH, '1')
                origin = described['memories'][0]['origin']
                self.assertEqual(origin['group_id'], '123')
                self.assertEqual(origin['participants'], [{'id': '2', 'name': '小明'}])
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_relation_model_conflict_becomes_candidate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                plugin.memory.apply_facts(
                    plugin.memory.DEFAULT_PATH, '1', 'private', 0,
                    [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                      'links': [], 'allowed': []}], event_id='seed')
                async def fake_embed(_text):
                    return [0.99, 0.1]
                plugin._embed = fake_embed
                fake = FakeCompletions(
                    extraction='{"memories":["用户不喜欢咖啡"]}',
                    relations='{"links":[{"from":0,"to":1,"kind":"contradicts","weight":0.9}]}',
                )
                version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
                with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                    asyncio.run(plugin._extract('1', '用户不喜欢咖啡', 'private', version))
                self.assertEqual(
                    len(plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '1')), 1)
                candidate = plugin.memory.list_candidates(plugin.memory.DEFAULT_PATH, '1')[0]
                self.assertEqual(candidate['reason'], 'conflict')
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_relation_model_failure_keeps_saved_memory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                plugin.memory.apply_facts(
                    plugin.memory.DEFAULT_PATH, '1', 'private', 0,
                    [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                      'links': [], 'allowed': []}], event_id='seed')
                async def fake_embed(_text):
                    return [0.8, 0.6]
                plugin._embed = fake_embed
                fake = FakeCompletions(extraction='{"memories":["用户每天喝咖啡"]}')
                version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
                with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                    asyncio.run(plugin._extract('1', '用户每天喝咖啡', 'private', version))
                self.assertEqual(
                    len(plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '1')), 2)
                relations = plugin.memory.describe_user(
                    plugin.memory.DEFAULT_PATH, '1')['memories'][0]['relations']
                self.assertEqual(relations[0]['kind'], 'related')
                self.assertEqual(relations[0]['origin'], 'vector')
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_legacy_edge_refinement_uses_relation_model(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                plugin.memory.store(plugin.memory.DEFAULT_PATH, '1',
                                    ['用户喜欢喝咖啡', '用户每天喝咖啡'], 'private', 0)
                vectors = {'用户喜欢喝咖啡': [1.0, 0.0], '用户每天喝咖啡': [0.95, 0.2]}
                for row in plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '1'):
                    plugin.memory.set_vector(plugin.memory.DEFAULT_PATH, '1',
                                             row['id'], row['text'], vectors[row['text']])
                self.assertEqual(plugin.memory.backfill_edges(plugin.memory.DEFAULT_PATH, '1'), 1)
                pair = plugin.memory.legacy_edge_candidates(plugin.memory.DEFAULT_PATH, '1')[0]

                async def fake_classify(_inspected):
                    return [{'from': 0, 'to': pair['b_id'],
                             'kind': 'corroborates', 'weight': 0.85}]

                with patch.object(plugin, '_classify_relations', new=fake_classify):
                    self.assertEqual(asyncio.run(plugin._refine_legacy_edges('1')), 1)
                relations = plugin.memory.describe_user(
                    plugin.memory.DEFAULT_PATH, '1')['memories'][0]['relations']
                self.assertEqual(relations[0]['kind'], 'corroborates')
                self.assertEqual(relations[0]['origin'], 'model')
                self.assertEqual(
                    plugin.memory.legacy_edge_candidates(plugin.memory.DEFAULT_PATH, '1'), [])
                with patch.object(plugin, '_classify_relations', new=fake_classify):
                    self.assertEqual(asyncio.run(plugin._refine_legacy_edges('1')), 0)
            finally:
                plugin.memory.DEFAULT_PATH = old

    def test_relation_model_refines_edge_is_persisted(self):
        with _memory_env():
            async def fake_embed(_text):
                return [0.8, 0.6]
            plugin._embed = fake_embed
            seed = plugin.memory.apply_facts(
                plugin.memory.DEFAULT_PATH, '1', 'private', 0,
                [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                  'links': [], 'allowed': []}], event_id='seed')[0]['id']
            fake = FakeCompletions(
                extraction='{"memories":["用户每天喝咖啡"]}',
                relations=json.dumps({'links': [
                    {'from': 0, 'to': seed, 'kind': 'refines', 'weight': 0.85}]}))
            version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
            with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                asyncio.run(plugin._extract('1', '用户每天喝咖啡', 'private', version,
                                            event_id='e2'))
            relations = plugin.memory.describe_user(
                plugin.memory.DEFAULT_PATH, '1')['memories'][0]['relations']
            self.assertEqual([(rel['kind'], rel['origin']) for rel in relations],
                             [('refines', 'model')])

    def test_relation_model_unrelated_edge_is_persisted(self):
        with _memory_env():
            async def fake_embed(_text):
                return [0.8, 0.6]
            plugin._embed = fake_embed
            seed = plugin.memory.apply_facts(
                plugin.memory.DEFAULT_PATH, '1', 'private', 0,
                [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                  'links': [], 'allowed': []}], event_id='seed')[0]['id']
            fake = FakeCompletions(
                extraction='{"memories":["用户每天喝咖啡"]}',
                relations=json.dumps({'links': [
                    {'from': 0, 'to': seed, 'kind': 'unrelated', 'weight': 0.8}]}))
            version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
            with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                asyncio.run(plugin._extract('1', '用户每天喝咖啡', 'private', version,
                                            event_id='e2'))
            self.assertEqual(plugin.memory.describe_user(
                plugin.memory.DEFAULT_PATH, '1')['stats']['relations'], 0)
            with plugin.memory._connect(plugin.memory.DEFAULT_PATH) as con:
                self.assertEqual(tuple(con.execute(
                    'SELECT kind,origin FROM memory_edges').fetchone()),
                    ('unrelated', 'model'))

    def test_independent_corroboration_keeps_memory_and_edge(self):
        with _memory_env():
            async def fake_embed(_text):
                return [0.8, 0.6]
            plugin._embed = fake_embed
            seed = plugin.memory.apply_facts(
                plugin.memory.DEFAULT_PATH, '1', 'private', 0,
                [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                  'links': [], 'allowed': []}], event_id='seed')[0]['id']
            fake = FakeCompletions(
                extraction='{"memories":["用户每天喝咖啡"]}',
                relations=json.dumps({'links': [
                    {'from': 0, 'to': seed, 'kind': 'corroborates', 'weight': 0.8}]}))
            version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
            with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                asyncio.run(plugin._extract('1', '用户每天喝咖啡', 'private', version,
                                            event_id='e2'))
            described = plugin.memory.describe_user(plugin.memory.DEFAULT_PATH, '1')
            self.assertEqual(len(described['memories']), 2)
            relations = described['memories'][0]['relations']
            self.assertEqual([(rel['kind'], rel['origin']) for rel in relations],
                             [('corroborates', 'model')])

    def test_legacy_refinement_discards_result_after_correction(self):
        with _memory_env():
            plugin.memory.store(plugin.memory.DEFAULT_PATH, '1',
                                ['用户喜欢喝咖啡', '用户每天喝咖啡'], 'private', 0)
            vectors = {'用户喜欢喝咖啡': [1.0, 0.0], '用户每天喝咖啡': [0.8, 0.6]}
            for row in plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '1'):
                plugin.memory.set_vector(plugin.memory.DEFAULT_PATH, '1',
                                         row['id'], row['text'], vectors[row['text']])
            self.assertEqual(plugin.memory.backfill_edges(plugin.memory.DEFAULT_PATH, '1'), 1)
            pair = plugin.memory.legacy_edge_candidates(plugin.memory.DEFAULT_PATH, '1')[0]

            async def fake_classify(_inspected):
                plugin.memory.correct(plugin.memory.DEFAULT_PATH, '1',
                                      pair['a_id'], '用户改喝咖啡')
                return [{'from': 0, 'to': pair['b_id'],
                         'kind': 'contradicts', 'weight': 0.9}]

            with patch.object(plugin, '_classify_relations', new=fake_classify):
                self.assertEqual(asyncio.run(plugin._refine_legacy_edges('1')), 0)
            with plugin.memory._connect(plugin.memory.DEFAULT_PATH) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_edges').fetchone()[0], 0)

    def test_capacity_conflict_precedes_capacity_candidate(self):
        with _memory_env():
            async def fake_embed(_text):
                return [1.0, 0.0]
            plugin._embed = fake_embed
            with patch.object(plugin.memory, 'MAX_MEMORIES', 2):
                plugin.memory.store(plugin.memory.DEFAULT_PATH, '1',
                                    ['用户喜欢咖啡', '用户喜欢喝茶'], 'private', 0)
                rows = {row['text']: row['id']
                        for row in plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '1')}
                low_id = rows['用户喜欢咖啡']
                high_id = rows['用户喜欢喝茶']
                plugin.memory.set_vector(plugin.memory.DEFAULT_PATH, '1', low_id,
                                         '用户喜欢咖啡', [0.8, 0.6])
                plugin.memory.set_vector(plugin.memory.DEFAULT_PATH, '1', high_id,
                                         '用户喜欢喝茶', [1.0, 0.0])
                fake = FakeCompletions(
                    extraction='{"memories":["用户不喜欢咖啡"]}',
                    relations=json.dumps({'links': [
                        {'from': 0, 'to': low_id, 'kind': 'contradicts', 'weight': 0.9}]}))
                version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
                with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                    asyncio.run(plugin._extract('1', '用户不喜欢咖啡', 'private', version,
                                                event_id='e2'))
                self.assertEqual(len(plugin.memory.list_memories(
                    plugin.memory.DEFAULT_PATH, '1')), 2)
                candidate = plugin.memory.list_candidates(plugin.memory.DEFAULT_PATH, '1')[0]
                self.assertEqual(candidate['reason'], 'conflict')
                self.assertEqual(candidate['similar_id'], low_id)

    def test_equivalent_corroboration_adds_observation(self):
        with _memory_env():
            async def fake_embed(_text):
                return [0.99, 0.1]
            plugin._embed = fake_embed
            origin = {'group_id': '123', 'bot_id': '1000', 'origin_ts': 1000,
                      'participants': ['2']}
            seed = plugin.memory.apply_facts(
                plugin.memory.DEFAULT_PATH, '1', 'group', 0,
                [{'action': 'save', 'text': '用户喜欢咖啡', 'vector': [1.0, 0.0],
                  'links': [], 'allowed': []}],
                event_id='e1', origin=origin)[0]['id']
            fake = FakeCompletions(
                extraction='{"memories":["用户很喜欢咖啡"]}',
                relations=json.dumps({'links': [
                    {'from': 0, 'to': seed, 'kind': 'corroborates', 'weight': 0.9}]}))
            version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
            with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                asyncio.run(plugin._extract('1', '用户很喜欢咖啡', 'group', version,
                                            origin=origin, event_id='e2'))
            described = plugin.memory.describe_user(plugin.memory.DEFAULT_PATH, '1')
            self.assertEqual(len(described['memories']), 1)
            self.assertEqual(described['memories'][0]['observation_count'], 2)
            self.assertEqual(described['candidates'], [])
            version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '1')
            with patch.object(plugin.model_dispatch, 'complete', side_effect=fake.create):
                asyncio.run(plugin._extract('1', '用户很喜欢咖啡', 'group', version,
                                            origin=origin, event_id='e2'))
            described = plugin.memory.describe_user(plugin.memory.DEFAULT_PATH, '1')
            self.assertEqual(described['memories'][0]['observation_count'], 2)

    def test_group_session_origin_survives_prompt_guard(self):
        with _memory_env():
            async def fake_embed(_text):
                return [1.0, 0.0]
            plugin._embed = fake_embed
            stamp = _stamp(STAMP_TS)
            old = _stamp(STAMP_TS - 3600)
            history = [
                ChatMessage(time=stamp, userid='1', groupid=GKEY,
                            message='<昵称> 我先说', respond='嗯'),
                ChatMessage(time=stamp, userid='2', groupid=GKEY,
                            message='<小明> 我也在', respond='好'),
                ChatMessage(time=old, userid='3', groupid=GKEY,
                            message='<迟到者> 一小时前', respond=''),
                ChatMessage(time=stamp, userid='4',
                            groupid=f'qq_OneBot V11_{SELF}_987654321',
                            message='<别群> 其他群', respond=''),
                ChatMessage(time=stamp, userid='5',
                            groupid=f'qq_OneBot V11_{SELF + 1}_{GRP}',
                            message='<别bot> 别的机器人', respond=''),
                ChatMessage(time=stamp, userid='6', groupid='-1',
                            message='私聊内容', respond=''),
            ]
            request = ModelRequest(prompt='<昵称> 我也觉得', history=history)
            event = _group_event('我也觉得', message_id=77)
            asyncio.run(prompt_guard.guard(request, event))
            asyncio.run(plugin.inject(request, event))
            asyncio.run(plugin.collect(SimpleNamespace(respond='收到'), event))
            job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
            self.assertEqual(job['origin_group_id'], str(GRP))
            self.assertEqual(job['origin_bot_id'], str(SELF))
            self.assertEqual(int(job['origin_ts']), STAMP_TS)
            self.assertEqual(json.loads(job['origin_participants']), ['2'])
            with patch.object(plugin.model_dispatch, 'complete',
                              side_effect=FakeCompletions().create):
                asyncio.run(plugin._process_job(job))
            origin = plugin.memory.describe_user(
                plugin.memory.DEFAULT_PATH, '1')['memories'][0]['origin']
            self.assertEqual(origin['group_id'], str(GRP))
            self.assertEqual(origin['participants'], [{'id': '2', 'name': '小明'}])

    def test_placeholder_is_not_stored_as_part_of_the_message(self):
        """image_guard 补的 `[图片]` 不是用户说的话，不能进记忆正文。"""
        event = EventStub('42', 'private')
        event.get_plaintext = lambda: '[图片]这是啥'

        self.assertEqual(plugin._text(event), '这是啥')

    def test_plain_text_is_untouched(self):
        event = EventStub('42', 'private')

        self.assertEqual(plugin._text(event), event.get_plaintext().strip())

    def test_message_starting_with_bracket_text_is_kept(self):
        """只有开头那一个占位符被剥掉，正文里本来就有的方括号字样不能动。"""
        event = EventStub('42', 'private')
        event.get_plaintext = lambda: '[图片]就是字面意思'

        self.assertEqual(plugin._text(event), '就是字面意思')


if __name__ == '__main__':
    unittest.main()
