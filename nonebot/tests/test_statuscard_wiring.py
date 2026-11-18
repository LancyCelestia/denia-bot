# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""接线用例：命令规则、权限、配置合并、运行时初始化与定时判定。

只验「接线有没有接对」，不重复逻辑层的断言。需要 nonebot 初始化，所以先
`nonebot.init()` 再 import 插件——与仓库里其它 wiring 用例一致。
"""

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot

# 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）。
try:
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from plugins import statuscard as plugin


def event(text, user_id=1000, group_id=555):
    message = Message([MessageSegment.text(text)])
    return GroupMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=user_id, group_id=group_id, message_type='group', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test', 'role': 'owner'}, to_me=False,
    )


class CommandTest(unittest.TestCase):
    def test_command_strips_prefixes(self):
        self.assertEqual(plugin._command(event('查看状态')), '查看状态')
        self.assertEqual(plugin._command(event(' /查看状态 ')), '查看状态')
        self.assertEqual(plugin._command(event('.查看状态')), '查看状态')

    def test_rule_matches_only_known_commands(self):
        self.assertTrue(asyncio.run(plugin._rule(event('查看状态'))))
        self.assertTrue(asyncio.run(plugin._rule(event('状态卡诊断'))))
        self.assertTrue(asyncio.run(plugin._rule(event('查看集群状态'))))
        self.assertFalse(asyncio.run(plugin._rule(event('查看状态 一下'))))
        self.assertFalse(asyncio.run(plugin._rule(event('查看集群状态 一下'))))
        self.assertFalse(asyncio.run(plugin._rule(event('今天天气'))))

    def test_admin_only(self):
        self.assertTrue(plugin._is_admin(event('查看状态', user_id=1000)))
        self.assertFalse(plugin._is_admin(event('查看状态', user_id=2000)))

    def test_matcher_is_high_priority_and_blocking(self):
        self.assertEqual(plugin.matcher.priority, 1)
        self.assertTrue(plugin.matcher.block)

    def test_command_reloads_config_before_dispatch(self):
        """面板保存后立刻发命令，不能还在用旧配置（集群开关曾因此读成 false）。"""
        from unittest.mock import patch as mock_patch

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.config['cluster'] = {'enabled': False}
            old = plugin.runtime
            plugin.runtime = runtime
            calls = []

            def reload_now():
                runtime.config['cluster']['enabled'] = True
                calls.append('reload')
                return True

            async def fake_send(bot, evt):
                calls.append(('send', runtime.config['cluster']['enabled']))

            try:
                with mock_patch.object(runtime, 'maybe_reload', reload_now), \
                        mock_patch.object(plugin, 'send_cluster_card', fake_send):
                    asyncio.run(plugin.handle_command(None, event('查看集群状态')))
            finally:
                plugin.runtime = old

        self.assertEqual(calls, ['reload', ('send', True)])


class ConfigTest(unittest.TestCase):
    def test_defaults_fill_missing_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            path.write_text(json.dumps({'alerts': {'enabled': True}}), encoding='utf-8')
            config = plugin.load_config(path)
            self.assertTrue(config['alerts']['enabled'])
            self.assertEqual(config['display']['width'], 1170, '缺的字段要补默认值')
            self.assertIn('report', config)

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            config = plugin.load_config(path)
            config['display']['width'] = 1440
            plugin.save_config(config, path)
            again = plugin.load_config(path)
            self.assertEqual(again['display']['width'], 1440)

    def test_loaded_config_does_not_share_defaults(self):
        """改一份读出来的配置，不能影响下一份读出来的默认值。"""
        with tempfile.TemporaryDirectory() as folder:
            first = plugin.load_config(Path(folder) / 'a.json')
            first['alerts']['enabled'] = True
            first['report']['sections'].append('llm')
            second = plugin.load_config(Path(folder) / 'b.json')
            self.assertFalse(second['alerts']['enabled'])
            self.assertNotIn('llm', second['report']['sections'])

    def test_corrupt_config_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            path.write_text('{broken', encoding='utf-8')
            config = plugin.load_config(path)
            self.assertEqual(config['display']['width'], 1170)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_lazy_does_not_create_database(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        self.assertIsNone(runtime.samples)
        self.assertFalse((self.base / 'statuscard.sqlite3').exists())
        with self.assertRaises(RuntimeError):
            _ = runtime.sample_store

    def test_prepare_creates_store_and_loads_counters(self):
        seed = plugin.Runtime(base=self.base, lazy=False)
        seed.counters = {'recv': 3, 'sent': 5}
        seed.flush_counters()
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.prepare()
        self.assertIsNotNone(runtime.samples)
        self.assertEqual(runtime.counters, {'recv': 3, 'sent': 5})

    def test_targets_fall_back_to_alerts(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.config['alerts']['targets'] = ['123456']
        runtime.config['monitors']['targets'] = []
        self.assertEqual(runtime.targets('monitor'), [{'kind': 'group', 'id': 123456}])
        runtime.config['monitors']['targets'] = ['private:777']
        self.assertEqual(runtime.targets('monitor'), [{'kind': 'private', 'id': 777}])

    def test_counters_bump_and_flush(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.prepare()
        runtime.bump('recv')
        runtime.bump('sent', 2)
        runtime.flush_counters()
        again = plugin.Runtime(base=self.base, lazy=False)
        self.assertEqual(again.counters, {'recv': 1, 'sent': 2})


class ScheduleTest(unittest.TestCase):
    def test_due_respects_clock_marker_and_first_day(self):
        self.assertFalse(plugin._due('', '2026-10-30', '08:59', (9, 0), ''))
        self.assertTrue(plugin._due('', '2026-10-30', '09:00', (9, 0), ''))
        self.assertFalse(plugin._due('2026-10-30', '2026-10-30', '09:00', (9, 0), ''))
        self.assertTrue(plugin._due('2026-10-29', '2026-10-30', '09:00', (9, 0), ''))
        # 首次安装当天不补发
        self.assertFalse(plugin._due('2026-10-29', '2026-10-30', '09:00', (9, 0), '2026-10-30'))

    def test_report_targets_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.config['alerts']['targets'] = ['123']
            self.assertEqual(plugin._report_targets(runtime), [{'kind': 'group', 'id': 123}])


class LifecycleTest(unittest.TestCase):
    def test_startup_creates_tasks_and_shutdown_cancels(self):
        from unittest.mock import patch

        async def idle(*_args, **_kwargs):
            await asyncio.sleep(3600)

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                async def scenario():
                    with patch.multiple(plugin, sample_loop=idle, alert_loop=idle,
                                        monitor_loop=idle, schedule_loop=idle),                          patch.object(plugin.collect, 'prime_cpu', lambda: None),                          patch.object(plugin.collect, 'prime_process_cpu', lambda: None):
                        await plugin._startup()
                        self.assertEqual(len(runtime.tasks), 4)
                        self.assertIsNotNone(runtime.samples, '启动钩子要建采样库')
                        await asyncio.sleep(0.05)
                        await plugin._shutdown()
                asyncio.run(scenario())
                self.assertEqual(runtime.tasks, [], '关闭钩子要把任务清空')
            finally:
                plugin.runtime = old


class MonitorWiringTest(unittest.TestCase):
    def test_suppress_when_down_marks_dependent_item(self):
        from unittest.mock import patch as mock_patch

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=False)
            runtime.prepare()
            runtime.config['monitors'].update({
                'enabled': True,
                'fail_threshold': 1,
                'items': [
                    {'type': 'tcp', 'name': '网关', 'port': 1},
                    {'type': 'http', 'name': '接口', 'url': 'http://127.0.0.1:1/',
                     'suppress_when_down': '网关'},
                ],
            })

            async def fake_probe(item, max_response_bytes=0):
                return {'ok': False, 'detail': 'connection refused', 'config_error': False}

            with mock_patch.object(plugin.monitor, 'probe', fake_probe):
                results = asyncio.run(plugin.run_monitors(runtime, now=1700000000.0, notify=True))

        by_name = {item['name']: item for item in results}
        self.assertFalse(by_name['网关']['suppressed'], '没有依赖项的一项照常告警')
        self.assertTrue(by_name['接口']['suppressed'], '依赖项失败时要抑制本项')
        # 状态照样记录：抑制的是通知，不是事实
        self.assertTrue(runtime.state.section('monitor')['接口']['alerting'])


class SendCounterTest(unittest.TestCase):
    def test_only_successful_sends_are_counted(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def call_api(self, action, **params):
                if action == 'send_group_msg' and params.get('fail'):
                    raise RuntimeError('network')
                self.calls.append(action)
                return {}

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                bot = FakeBot()
                plugin._install_send_counter(bot)
                asyncio.run(bot.call_api('send_group_msg', group_id=1, message='x'))
                asyncio.run(bot.call_api('get_status'))
                with self.assertRaises(RuntimeError):
                    asyncio.run(bot.call_api('send_group_msg', fail=True))
                self.assertEqual(runtime.counters['sent'], 1, '只数成功发出的消息')
                plugin._install_send_counter(bot)   # 幂等，不能重复包
                asyncio.run(bot.call_api('send_private_msg', user_id=1, message='y'))
                self.assertEqual(runtime.counters['sent'], 2)
            finally:
                plugin.runtime = old


class DiagnoseTest(unittest.TestCase):
    def test_diagnose_reports_state(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            runtime.config['monitors']['items'] = [{'type': 'tcp', 'name': '缺端口'}]
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                text = plugin.diagnose()
            finally:
                plugin.runtime = old
            self.assertIn('状态卡诊断', text)
            self.assertIn('采样库', text)
            self.assertIn('监测', text)
            self.assertIn('缺端口', text, '不可用的监测项要在诊断里点名')

    def test_push_without_bot_is_false(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            ok = asyncio.run(plugin.push(runtime, [{'kind': 'group', 'id': 1}], '测试', label='测试'))
            self.assertFalse(ok)
            self.assertFalse(runtime.last_push['ok'])


class _Finished(Exception):
    """`matcher.finish` 的哨兵：抛出来表示「这句话已经回给用户了」。"""


class FakeBot:
    """只记 `send` 的假 bot；`error` 用来验证发送失败分支。"""

    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def send(self, event, message):
        if self.error is not None:
            raise self.error
        self.sent.append(message)


@contextlib.contextmanager
def _finish_sentinel():
    """把 `matcher.finish` 换成抛哨兵，用例就能断言回给用户的那句话。

    `matcher` 是 MatcherMeta 造出来的类，`finish` 继承自基类的 classmethod；
    用完必须撤掉这个影子属性，否则后面的用例会一直抛哨兵。
    """
    calls = []
    had_own = 'finish' in plugin.matcher.__dict__
    original = plugin.matcher.__dict__.get('finish')

    async def finish(message=None, **kwargs):
        calls.append(message)
        raise _Finished(message)

    plugin.matcher.finish = staticmethod(finish)
    try:
        yield calls
    except _Finished:
        # 哨兵只用来中断 `send_cluster_card`，用例只关心「回了哪句话」
        pass
    finally:
        if had_own:
            plugin.matcher.finish = original
        else:
            del plugin.matcher.finish


class ClusterViewTest(unittest.TestCase):
    """`build_cluster_view`：配置开关与传给数据层的参数。"""

    def setUp(self):
        from unittest.mock import patch as mock_patch
        self.mock_patch = mock_patch
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = plugin.Runtime(base=Path(self.folder.name), lazy=True)
        self.old = plugin.runtime
        plugin.runtime = self.runtime

    def tearDown(self):
        plugin.runtime = self.old
        self.folder.cleanup()

    def test_disabled_raises(self):
        self.runtime.config['cluster'] = {'enabled': False}
        with self.assertRaises(ValueError) as caught:
            asyncio.run(plugin.build_cluster_view(self.runtime))
        self.assertIn('未启用', str(caught.exception))

    def test_missing_section_raises(self):
        self.runtime.config.pop('cluster', None)
        with self.assertRaises(ValueError):
            asyncio.run(plugin.build_cluster_view(self.runtime))

    def test_enabled_calls_fetch_with_config(self):
        cluster_conf = {'enabled': True, 'title': 'Proxmox VE', 'bucket': 'proxmox'}
        grafana_conf = {'url': 'http://127.0.0.1:3000', 'datasource_uid': 'uid'}
        self.runtime.config['cluster'] = dict(cluster_conf)
        self.runtime.config['grafana'] = dict(grafana_conf)
        seen = []

        def fake_fetch(conf, grafana_conf, *, root=None):
            seen.append((conf, grafana_conf, root))
            return {'title': 'Proxmox VE', 'nodes': []}

        with self.mock_patch.object(plugin.cluster, 'fetch', fake_fetch):
            view = asyncio.run(plugin.build_cluster_view(self.runtime))

        self.assertEqual(view['title'], 'Proxmox VE')
        self.assertEqual(seen[0][0], cluster_conf)
        self.assertEqual(seen[0][1], grafana_conf, 'Grafana 设置要原样传下去')
        self.assertEqual(seen[0][2], plugin.ROOT)


class ClusterCommandTest(unittest.TestCase):
    """`send_cluster_card`：未启用、查询失败、出图失败、发送失败与独立冷却。"""

    def setUp(self):
        from unittest.mock import patch as mock_patch
        self.mock_patch = mock_patch
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = plugin.Runtime(base=Path(self.folder.name), lazy=True)
        self.runtime.config['display']['cooldown_seconds'] = 0
        self.runtime.config['cluster'] = {'enabled': True, 'title': 'Proxmox VE'}
        self.old = plugin.runtime
        plugin.runtime = self.runtime

    def tearDown(self):
        plugin.runtime = self.old
        self.folder.cleanup()

    def _run(self, bot=None):
        asyncio.run(plugin.send_cluster_card(bot or FakeBot(), event('查看集群状态')))

    def test_disabled_finishes_with_hint(self):
        self.runtime.config['cluster'] = {'enabled': False}
        with _finish_sentinel() as calls:
            self._run()
        self.assertEqual(len(calls), 1)
        self.assertIn('未启用', calls[0])
        self.assertIn('Proxmox VE 集群', calls[0], '提示要指向面板里的具体位置')

    def test_fetch_failure_returns_readable_text(self):
        def boom(*args, **kwargs):
            raise RuntimeError('Grafana 超时')

        with _finish_sentinel() as calls, self.mock_patch.object(plugin.cluster, 'fetch', boom):
            self._run()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith('集群状态读取失败：'))
        self.assertIn('Grafana 超时', calls[0])

    def test_fetch_failure_without_message_uses_last_error(self):
        def boom(*args, **kwargs):
            raise RuntimeError('')

        with _finish_sentinel() as calls, \
                self.mock_patch.object(plugin.cluster, 'fetch', boom), \
                self.mock_patch.object(plugin.grafana, 'last_error', lambda: '数据源未配置'):
            self._run()
        self.assertIn('数据源未配置', calls[0], '异常没有消息时要用 Grafana 记录的原因')

    def test_render_failure_falls_back_to_summary(self):
        view = {'title': 'Proxmox VE', 'summary': '1 节点 / 5 访客'}

        async def fake_view(runtime):
            return view

        def broken_render(*args, **kwargs):
            raise plugin.image.RenderError('页面渲染失败')

        with _finish_sentinel() as calls, \
                self.mock_patch.object(plugin, 'build_cluster_view', fake_view), \
                self.mock_patch.object(plugin.image, 'render_cluster_card_pages', broken_render), \
                self.mock_patch.object(plugin.cluster, 'summary_text', lambda v: '集群文字摘要'):
            self._run()
        self.assertEqual(calls, ['集群文字摘要'])

    def test_success_sends_image_at_display_width(self):
        widths = []

        async def fake_view(runtime):
            return {'title': 'Proxmox VE'}

        def fake_render(view, *, width=1170):
            widths.append(width)
            return [b'png-1']

        bot = FakeBot()
        with _finish_sentinel() as calls, \
                self.mock_patch.object(plugin, 'build_cluster_view', fake_view), \
                self.mock_patch.object(plugin.image, 'render_cluster_card_pages', fake_render):
            self._run(bot)

        self.assertEqual(calls, [], '成功路径不该走 finish')
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(widths, [int(self.runtime._display_conf().get('width') or 1170)])

    def test_send_failure_finishes_with_hint(self):
        async def fake_view(runtime):
            return {'title': 'Proxmox VE'}

        bot = FakeBot(error=RuntimeError('协议端断开'))
        with _finish_sentinel() as calls, \
                self.mock_patch.object(plugin, 'build_cluster_view', fake_view), \
                self.mock_patch.object(plugin.image, 'render_cluster_card_pages',
                                       lambda view, *, width=1170: [b'png']):
            self._run(bot)
        self.assertEqual(calls, ['图片发送失败，请检查协议端连接。'])

    def test_cooldown_blocks_second_fetch(self):
        self.runtime.config['display']['cooldown_seconds'] = 60
        fetched = []

        def fake_fetch(*args, **kwargs):
            fetched.append(1)
            raise RuntimeError('boom')

        with self.mock_patch.object(plugin.cluster, 'fetch', fake_fetch):
            with _finish_sentinel() as first:
                self._run()
            with _finish_sentinel() as second:
                self._run()

        self.assertIn('集群状态读取失败', first[0])
        self.assertIn('稍等', second[0])
        self.assertEqual(len(fetched), 1, '冷却期内不能再查一次')
        self.assertEqual(list(self.runtime.cooldowns),
                         [f'cluster:{event("查看集群状态").get_session_id()}'],
                         '冷却键要独立于状态卡的键')


if __name__ == '__main__':
    unittest.main()
