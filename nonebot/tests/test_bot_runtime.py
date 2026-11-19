"""bot.py 运行时修复的离线用例：NO_PROXY 清理与启动标识。

直接导入 bot.py 会初始化 NoneBot 并加载全部插件，所以这里用最小桩模块
执行入口文件，只验证运行时前段逻辑，不启动真实机器人、不发网络请求。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest

BOT_PATH = Path(__file__).resolve().parents[1] / 'bot.py'
MODULE_NAME = 'qqbot_bot_runtime_under_test'

_WATCHED_ENV = ('NO_PROXY', 'no_proxy', 'OPENAI_CUSTOM_HEADERS', 'QQBOT_START_TOKEN', 'GSUID_CORE_ENABLE')


class _FakeApp:
    def get(self, path, **kwargs):
        return lambda function: function


class _FakeAdapter:
    pass


def _fake_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def _install_stubs(monkeypatch):
    """用桩替换 NoneBot 与其它入口依赖，避免真实初始化。"""
    nonebot = _fake_module('nonebot')
    nonebot.__path__ = []
    driver = types.SimpleNamespace(register_adapter=lambda adapter: None)
    app = _FakeApp()
    nonebot.init = lambda **kwargs: None
    nonebot.get_driver = lambda: driver
    nonebot.get_app = lambda: app
    nonebot.get_bots = lambda: {}
    nonebot.load_plugin = lambda name: None

    adapters = _fake_module('nonebot.adapters')
    adapters.__path__ = []
    onebot = _fake_module('nonebot.adapters.onebot')
    onebot.__path__ = []
    v11 = _fake_module('nonebot.adapters.onebot.v11', Adapter=_FakeAdapter)
    nonebot.adapters = adapters
    adapters.onebot = onebot
    onebot.v11 = v11

    stubs = {
        'nonebot': nonebot,
        'nonebot.adapters': adapters,
        'nonebot.adapters.onebot': onebot,
        'nonebot.adapters.onebot.v11': v11,
        'conversation_header': _fake_module('conversation_header', configure=lambda: ''),
        'private_env': _fake_module('private_env', get_private=lambda key: ''),
        'command_policy': _fake_module('command_policy', install_command_rules=lambda: None),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)


def _load_bot(monkeypatch):
    _install_stubs(monkeypatch)
    # 关掉 GenshinUID 分支，桩里不需要再模拟它的 receive 模块。
    monkeypatch.setenv('GSUID_CORE_ENABLE', '0')
    spec = importlib.util.spec_from_file_location(MODULE_NAME, BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _restore_process_environment():
    """bot.py 在导入时可能改写 NO_PROXY / OPENAI_CUSTOM_HEADERS，用例结束后还原。"""
    saved = {key: os.environ.get(key) for key in _WATCHED_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_normalize_no_proxy_removes_loopback_entries(monkeypatch):
    bot = _load_bot(monkeypatch)
    assert bot.normalize_no_proxy('localhost, [::1],127.0.0.1,::1') == 'localhost,127.0.0.1'
    assert bot.normalize_no_proxy('[::1] localhost  ::1') == 'localhost'
    assert bot.normalize_no_proxy('[::1]') == ''
    assert bot.normalize_no_proxy('') == ''


def test_normalize_no_proxy_preserves_other_values_and_is_idempotent(monkeypatch):
    bot = _load_bot(monkeypatch)
    untouched = 'localhost,::10,[::1]:8080,example.com'
    assert bot.normalize_no_proxy(untouched) == untouched

    cleaned = bot.normalize_no_proxy('localhost,[::1],example.com')
    assert cleaned == 'localhost,example.com'
    assert bot.normalize_no_proxy(cleaned) == cleaned


def test_sanitize_proxy_environment_covers_both_names(monkeypatch):
    bot = _load_bot(monkeypatch)
    env = {'NO_PROXY': 'localhost, [::1]', 'no_proxy': '::1 example.com'}
    assert bot.sanitize_proxy_environment(env) == ['NO_PROXY', 'no_proxy']
    assert env == {'NO_PROXY': 'localhost', 'no_proxy': 'example.com'}
    assert bot.sanitize_proxy_environment(env) == []


def test_import_cleans_process_no_proxy(monkeypatch):
    monkeypatch.setenv('NO_PROXY', 'localhost, [::1]')
    _load_bot(monkeypatch)
    assert os.environ.get('NO_PROXY') == 'localhost'


def test_start_token_prefers_restart_environment(monkeypatch):
    monkeypatch.setenv('QQBOT_START_TOKEN', 'panel-restart-token')
    bot = _load_bot(monkeypatch)
    assert bot.START_TOKEN == 'panel-restart-token'
    assert bot.resolve_start_token({'QQBOT_START_TOKEN': 'explicit-token'}) == 'explicit-token'

    payload = asyncio.run(bot.dashboard_health())
    assert payload['start_token'] == 'panel-restart-token'


def test_normal_launch_generates_start_token_once(monkeypatch):
    monkeypatch.delenv('QQBOT_START_TOKEN', raising=False)
    bot = _load_bot(monkeypatch)
    assert re.fullmatch(r'[A-Za-z0-9_-]{16,}', bot.START_TOKEN)
    # 空白环境值等同缺失，仍要生成可用令牌。
    assert bot.resolve_start_token({'QQBOT_START_TOKEN': '   '})

    first = asyncio.run(bot.dashboard_health())['start_token']
    second = asyncio.run(bot.dashboard_health())['start_token']
    assert first == second == bot.START_TOKEN


def test_health_payload_includes_identity_fields(monkeypatch):
    monkeypatch.delenv('QQBOT_START_TOKEN', raising=False)
    bot = _load_bot(monkeypatch)
    payload = asyncio.run(bot.dashboard_health())
    assert {'service', 'ready', 'connected', 'pid', 'start_token', 'started_at'} <= set(payload)
    assert payload['service'] == 'qqbot-nonebot'
    assert payload['ready'] is True
    assert payload['pid'] == os.getpid()
    assert payload['start_token'] == bot.START_TOKEN
    assert isinstance(payload['started_at'], (int, float))
    assert payload['started_at'] == bot.STARTED_AT
    assert payload['started_at'] > 0


def test_connected_still_uses_private_identity(monkeypatch):
    bot = _load_bot(monkeypatch)
    monkeypatch.setattr(bot, 'get_private', lambda key: '10001')
    monkeypatch.setattr(bot.nonebot, 'get_bots', lambda: {'10001': object(), '20002': object()})
    assert asyncio.run(bot.dashboard_health())['connected'] is True

    monkeypatch.setattr(bot.nonebot, 'get_bots', lambda: {'20002': object()})
    assert asyncio.run(bot.dashboard_health())['connected'] is False

    monkeypatch.setattr(bot, 'get_private', lambda key: '')
    assert asyncio.run(bot.dashboard_health())['connected'] is True
