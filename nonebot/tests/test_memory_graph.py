import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_logic as m


def origin(group='123', ts=1000, participants=()):
    return {'group_id': group, 'bot_id': '1000', 'origin_ts': ts,
            'participants': list(participants)}


class MemoryGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = Path(self.tmp.name) / 'memory.sqlite3'

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, uid, text, vector, *, group='123', ts=1000, participants=(), event='e'):
        return m.apply_facts(self.db, uid, 'group', 0,
                             [{'action': 'save', 'text': text, 'vector': vector,
                               'links': [], 'allowed': []}],
                             event_id=event, origin=origin(group, ts, participants))

    def test_observations_and_vector_edges(self):
        m.store(self.db, '1', ['用户喜欢喝咖啡', '用户每天喝咖啡'], 'private', 0)
        vectors = {'用户喜欢喝咖啡': [1, 0], '用户每天喝咖啡': [0.95, 0.2]}
        for row in m.list_memories(self.db, '1'):
            m.set_vector(self.db, '1', row['id'], row['text'], vectors[row['text']])

        self.assertEqual(m.backfill_edges(self.db, '1'), 1)
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 1)
        self.assertEqual(m.backfill_edges(self.db, '1'), 0)
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 1)

    def test_exact_duplicate_is_observation_not_memory(self):
        saved = self.save('1', '用户喜欢喝咖啡', [1, 0], event='e1')
        memory_id = saved[0]['id']
        observe = {'action': 'observe', 'text': '用户喜欢喝咖啡', 'memory_id': memory_id}
        m.apply_facts(self.db, '1', 'group', 0, [observe], event_id='e2',
                      origin=origin(ts=1001))
        m.apply_facts(self.db, '1', 'group', 0, [observe], event_id='e2',
                      origin=origin(ts=1002))
        self.assertEqual(m.describe_user(self.db, '1')['memories'][0]['observation_count'], 2)
        for index in range(25):
            m.apply_facts(self.db, '1', 'group', 0, [observe], event_id=f'e{index + 3}',
                          origin=origin(ts=1003 + index))
        self.assertEqual(m.describe_user(self.db, '1')['memories'][0]['observation_count'], 20)

    def test_conflict_candidate_then_accept_creates_edge(self):
        saved = self.save('1', '用户喜欢咖啡', [1, 0], event='e1')
        outcomes = m.apply_facts(self.db, '1', 'group', 0,
                                 [{'action': 'candidate', 'text': '用户不喜欢咖啡',
                                   'similar_id': saved[0]['id'], 'reason': 'conflict',
                                   'weight': 0.9}],
                                 event_id='e2', origin=origin(ts=1001))
        self.assertEqual(outcomes[0]['status'], 'candidate')
        candidate = m.list_candidates(self.db, '1')[0]
        self.assertEqual(candidate['reason'], 'conflict')
        self.assertEqual(candidate['similar_text'], '用户喜欢咖啡')
        self.assertTrue(m.resolve_candidate(self.db, '1', candidate['id'], 'accept'))
        self.assertEqual(len(m.list_memories(self.db, '1')), 2)
        stats = m.describe_user(self.db, '1')['stats']
        self.assertEqual(stats['conflicts'], 1)
        kinds = [rel['kind'] for rel in m.describe_user(self.db, '1')['memories'][0]['relations']]
        self.assertIn('contradicts', kinds)

    def test_conflict_candidate_replace_updates_text_and_clears_old_graph(self):
        first = self.save('1', '用户喜欢喝咖啡', [1, 0], event='e1')
        self.save('1', '用户每天喝咖啡', [0.95, 0.2], event='e2', ts=1001)
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 1)
        m.apply_facts(self.db, '1', 'group', 0,
                      [{'action': 'candidate', 'text': '用户喜欢喝茶',
                        'similar_id': first[0]['id'], 'reason': 'conflict',
                        'weight': 0.9}],
                      event_id='e3', origin=origin(ts=1002))
        candidate = m.list_candidates(self.db, '1')[0]
        self.assertTrue(m.resolve_candidate(self.db, '1', candidate['id'], 'replace'))
        rows = m.list_memories(self.db, '1')
        self.assertEqual({row['text'] for row in rows}, {'用户喜欢喝茶', '用户每天喝咖啡'})
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 0)
        self.assertEqual(m.list_candidates(self.db, '1'), [])
        with m._connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_graph_state').fetchone()[0], 0)

    def test_cross_user_shared_context_edge_is_admin_only(self):
        self.save('1', '用户喜欢咖啡', [1, 0], ts=1000, participants=['2'], event='e1')
        self.save('2', '用户也喜欢咖啡', [0.99, 0.1], ts=1010, participants=['1'], event='e2')
        described = m.describe_user(self.db, '1')
        self.assertEqual(described['stats']['cross_relations'], 1)
        relation = described['memories'][0]['relations'][0]
        self.assertEqual(relation['scope'], 'cross')
        self.assertEqual(relation['user_id'], '2')
        self.assertEqual(relation['kind'], 'shared_context')
        recalled = m.recall(self.db, '1', [1, 0], '咖啡')
        self.assertEqual([row['text'] for row in recalled], ['用户喜欢咖啡'])

    def test_parse_relations_rejects_invalid_and_dedupes(self):
        raw = '''```json
{"links":[
 {"from":0,"to":2,"kind":"related","weight":0.7},
 {"from":0,"to":2,"kind":"contradicts","weight":0.6},
 {"from":1,"to":2,"kind":"corroborates","weight":0.9},
 {"from":0,"to":9,"kind":"corroborates","weight":0.8},
 {"from":0,"to":2,"kind":"bogus","weight":0.8},
 {"from":true,"to":2,"kind":"related","weight":0.8}
]}
```'''
        links = m.parse_relations(raw, {0: {2}, 1: {2}})
        self.assertEqual(links, [
            {'from': 0, 'to': 2, 'kind': 'contradicts', 'weight': 0.6},
            {'from': 1, 'to': 2, 'kind': 'corroborates', 'weight': 0.9},
        ])
        self.assertEqual(m.parse_relations('not json', {0: {2}}), [])

    def test_correct_and_forget_remove_edges_and_observations(self):
        first = self.save('1', '用户喜欢喝咖啡', [1, 0], event='e1')
        self.save('1', '用户每天喝咖啡', [0.95, 0.2], event='e2', ts=1001)
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 1)
        self.assertTrue(m.correct(self.db, '1', first[0]['id'], '用户喜欢喝茶'))
        self.assertEqual(m.describe_user(self.db, '1')['stats']['relations'], 0)
        with m._connect(self.db) as con:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM memory_observations WHERE memory_id=?',
                (first[0]['id'],)).fetchone()[0], 0)
        self.assertTrue(m.forget(self.db, '1', '全部'))
        self.assertEqual(m.list_memories(self.db, '1'), [])
        with m._connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_edges').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_observations').fetchone()[0], 0)

    def test_describe_user_stats_and_origin_names(self):
        m.remember_people(self.db, '123', {'2': '小明'})
        first = self.save('1', '用户喜欢咖啡', [1, 0], ts=1000, participants=['2'], event='e1')
        self.save('1', '用户每天喝咖啡', [0.95, 0.2], ts=1010, participants=['2'], event='e2')
        self.save('2', '用户也喜欢咖啡', [0.99, 0.1], ts=1020, participants=['1'], event='e3')
        described = m.describe_user(self.db, '1')
        self.assertEqual(described['stats'], {
            'memories': 2, 'relations': 1, 'conflicts': 0,
            'cross_relations': 2, 'observations': 2,
        })
        item = next(row for row in described['memories'] if row['id'] == first[0]['id'])
        self.assertEqual(item['origin']['group_id'], '123')
        self.assertEqual(item['origin']['participants'], [{'id': '2', 'name': '小明'}])

    def test_legacy_edges_are_refined_once(self):
        m.store(self.db, '1', ['用户喜欢喝咖啡', '用户每天喝咖啡'], 'private', 0)
        vectors = {'用户喜欢喝咖啡': [1, 0], '用户每天喝咖啡': [0.95, 0.2]}
        for row in m.list_memories(self.db, '1'):
            m.set_vector(self.db, '1', row['id'], row['text'], vectors[row['text']])
        self.assertEqual(m.backfill_edges(self.db, '1'), 1)
        pairs = m.legacy_edge_candidates(self.db, '1')
        self.assertEqual(len(pairs), 1)
        self.assertEqual(m.refine_edges(self.db, '1', [
            {'a': pairs[0]['a_id'], 'b': pairs[0]['b_id'],
             'kind': 'corroborates', 'weight': 0.85},
        ]), 1)
        relations = m.describe_user(self.db, '1')['memories'][0]['relations']
        self.assertEqual(relations[0]['kind'], 'corroborates')
        self.assertEqual(relations[0]['origin'], 'model')
        self.assertEqual(m.legacy_edge_candidates(self.db, '1'), [])
        self.assertEqual(m.backfill_edges(self.db, '1'), 0)
        self.assertEqual(m.describe_user(self.db, '1')['memories'][0]['relations'][0]['kind'],
                         'corroborates')


if __name__ == '__main__':
    unittest.main()
