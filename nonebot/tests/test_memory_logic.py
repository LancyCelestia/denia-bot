import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_logic as ml


class MemoryTest(unittest.TestCase):
    def test_user_isolation_persistence_dedup_and_forget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db = Path(folder) / 'memories.sqlite3'
            version = ml.revision(db, '100')
            self.assertEqual(ml.store(db, '100', ['喜欢咖啡', '喜欢咖啡'], 'private', version), 1)
            self.assertEqual(ml.list_memories(db, '100')[0]['text'], '喜欢咖啡')
            self.assertEqual(ml.list_memories(db, '200'), [])
            self.assertTrue(ml.forget(db, '100', '全部'))
            self.assertEqual(ml.list_memories(db, '100'), [])
            self.assertEqual(ml.store(db, '100', ['旧的异步任务'], 'private', version), 0)

    def test_validated_extraction_and_private_group_annotation(self):
        self.assertEqual(ml.parse_extraction('{"memories":["喜欢咖啡", "喜欢咖啡", ""]}'), ['喜欢咖啡'])
        self.assertEqual(ml.parse_extraction('not json'), [])
        rendered = ml.render_memories([{'text': '喜欢咖啡', 'source': 'private'}], in_group=True)
        self.assertIn('私聊', rendered)
        self.assertIn('不要主动', rendered)

    def test_parse_relations_clamps_weights_and_prefers_conflicts(self):
        links = ml.parse_relations(
            '{"links":[{"from":0,"to":5,"kind":"related","weight":2},'
            '{"from":0,"to":5,"kind":"contradicts","weight":0.6}]}',
            {0: {5}})
        self.assertEqual(links, [{'from': 0, 'to': 5, 'kind': 'contradicts', 'weight': 0.6}])
        self.assertEqual(ml.parse_relations('{"links":[]}', {}), [])

    def test_remember_people_defaults_private_group_and_drops_invalid_ids(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db = Path(folder) / 'memories.sqlite3'
            self.assertEqual(ml.remember_people(db, None, {'1': '小明', 'bad id': '无效', '2': ''}), 1)
            self.assertEqual(ml.remember_people(db, 'bad id', {'1': '小明'}), 0)
            with ml._connect(db) as con:
                rows = [tuple(row) for row in con.execute(
                    'SELECT user_id,group_id,display_name FROM memory_people')]
            self.assertEqual(rows, [('1', '-1', '小明')])


if __name__ == '__main__':
    unittest.main()
