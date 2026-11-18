import asyncio
import os
import sys
import tempfile
import unittest
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
from plugins import memory as plugin


class EventStub:
    def __init__(self, uid, kind):
        self.uid, self.message_type = uid, kind

    def get_user_id(self):
        return self.uid

    def get_plaintext(self):
        return '我喜欢喝咖啡'

    self_id = 10000001
    message_id = 123456


class FakeCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"memories":["用户喜欢喝咖啡"]}'))])


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
