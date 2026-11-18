"""接收目标只约束投递与新建订阅；搜索/选择/列表在任何会话都能用。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bilibili.model import Page, Scope
from bilibili.service import Service

TARGET = ('1', 'private', '2')
GROUP = Scope('1', 'group', '1108838060')


class Store:
    stopped = False
    source_stopped = False

    def __init__(self):
        self.rows = []
        self.claimed = []
        self.added = []

    def require(self): pass

    def subscriptions(self, scope=None):
        if scope is None:
            return list(self.rows)
        return [r for r in self.rows if Scope(r['bot'], r['kind'], r['target']) == scope]

    def add(self, scope, uid, name, baseline, items):
        self.added.append((scope, uid))
        self.rows.append({'bot': scope.bot, 'kind': scope.kind, 'target': scope.target, 'uid': uid})

    def claim(self, scope, now, interval):
        self.claimed.append(scope)
        return None

    def complete(self, row, receipt, now): pass


class Client:
    restricted = False

    def __init__(self):
        self.searches = []
        self.validated = []
        self.fetched = []

    async def search(self, keyword):
        self.searches.append(keyword)
        return ('守岸人测试视频\nUP：某UP\n简介\nhttps://www.bilibili.com/video/BV1xx411c7mD',)

    async def validate_user(self, uid):
        self.validated.append(uid)
        return '某UP主'

    async def fetch_page(self, uid, cursor=None):
        self.fetched.append(uid)
        return Page(items=(), cursor=None)


class Sender:
    def available(self, scope): return True

    async def send(self, scope, item):
        raise AssertionError('目标之外的会话不能投递')


def service(targets=None, rows=()):
    store, client, sender = Store(), Client(), Sender()
    store.rows = list(rows)
    return Service(store, client, sender, targets=targets), store, client


class SearchOutsideTargetsTest(unittest.IsolatedAsyncioTestCase):
    """BILI_TARGETS 只决定投递；群聊里的 .b站搜索 之前被整体拒掉，是过宽的拦截。"""

    async def test_search_runs_in_group_outside_targets(self):
        runtime, _, client = service({TARGET})
        reply = await runtime.dispatch(GROUP, '42', '.b站搜索 守岸人')
        self.assertIn('使用 .b站选 1', reply)
        self.assertEqual(client.searches, ['守岸人'])

    async def test_select_runs_in_group_outside_targets(self):
        runtime, _, _ = service({TARGET})
        await runtime.dispatch(GROUP, '42', '.b站搜索 守岸人')
        reply = await runtime.dispatch(GROUP, '42', '.b站选 1')
        self.assertIn('BV1xx411c7mD', reply)

    async def test_list_runs_in_group_outside_targets(self):
        runtime, _, _ = service({TARGET})
        reply = await runtime.dispatch(GROUP, '42', '.b站订阅列表')
        self.assertEqual(reply, '当前会话暂无订阅。')

    async def test_subscribe_outside_targets_is_still_refused(self):
        runtime, store, client = service({TARGET})
        reply = await runtime.dispatch(GROUP, '42', '.b站订阅 476092297', is_owner=True)
        self.assertEqual(reply, '本次未创建订阅。当前 bot/会话未在接收目标配置中。')
        self.assertEqual(store.added, [])
        self.assertEqual(client.validated, [])

    async def test_subscribe_inside_targets_proceeds(self):
        runtime, store, _ = service({TARGET})
        reply = await runtime.dispatch(Scope(*TARGET), '42', '.b站订阅 476092297', is_owner=True)
        self.assertIn('已订阅 UID 476092297', reply)
        self.assertEqual([str(scope) for scope, _ in store.added], [str(Scope(*TARGET))])


class DeliveryGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_skips_scope_outside_targets(self):
        runtime, store, _ = service({TARGET}, rows=[{'bot': '1', 'kind': 'group', 'target': '1108838060'}])
        await runtime._deliver_round(store.rows)
        self.assertEqual(store.claimed, [])

    async def test_tick_does_not_scan_scope_outside_targets(self):
        runtime, store, client = service({TARGET}, rows=[{'bot': '1', 'kind': 'group', 'target': '1108838060'}])
        await runtime.tick()
        self.assertEqual(client.fetched, [])
        self.assertEqual(store.claimed, [])


if __name__ == '__main__':
    unittest.main()
