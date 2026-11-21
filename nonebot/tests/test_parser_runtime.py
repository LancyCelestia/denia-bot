import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from media_parser.core.data import ParseResult, Platform
from media_parser.runtime import ArbiterBot, ParserRuntime


def seg(kind, **data):
    return SimpleNamespace(type=kind, data=data)


class ParserRuntimeTests(unittest.TestCase):
    def test_group_arbitration_switch_controls_processing(self):
        async def run():
            runtime = ParserRuntime()
            try:
                event = SimpleNamespace(self_id=10, user_id=20, group_id=30, message_id=40, time=1,
                                        original_message=[seg('text', text='BV1xx411c7mD')], reply=None)
                match = runtime.match(event)
                parser = runtime.parsers[match[0]]
                parser.parse = AsyncMock(return_value=ParseResult(platform=Platform('bilibili', 'B站'), title='test', url='https://example.com/a'))
                runtime.sender.send_parse_result = AsyncMock()
                runtime.arbiter.compete = AsyncMock(return_value=False)
                runtime.cfg.arbiter = True
                await runtime.process(SimpleNamespace(), event, match)
                runtime.arbiter.compete.assert_awaited_once()
                parser.parse.assert_not_awaited()
                runtime.cfg.arbiter = False
                await runtime.process(SimpleNamespace(), event, match)
                self.assertEqual(runtime.arbiter.compete.await_count, 1)
                parser.parse.assert_awaited_once()
                runtime.sender.send_parse_result.assert_awaited_once()
            finally:
                await runtime.close()
        asyncio.run(run())

    def test_parse_error_replies_without_leaking_exception(self):
        async def run():
            runtime = ParserRuntime()
            try:
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='https://xhslink.com/a/example')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                runtime.parsers[match[0]].parse = AsyncMock(side_effect=RuntimeError('private-secret'))
                bot = SimpleNamespace(send=AsyncMock())
                await runtime.process(bot, event, match)
                bot.send.assert_awaited_once()
                text = str(bot.send.await_args.args[1])
                self.assertIn('解析失败', text)
                self.assertNotIn('private-secret', text)
            finally:
                await runtime.close()
        asyncio.run(run())

    def test_arbiter_onebot_parameters(self):
        async def run():
            bot = SimpleNamespace(call_api=AsyncMock(return_value={}))
            adapter = ArbiterBot(bot)
            await adapter.set_msg_emoji_like(message_id=1, emoji_id=289, emoji_type='1', set=True)
            await adapter.fetch_emoji_like(message_id=1, emojiId='289', emoji_id='289', emojiType='1', count=20)
            bot.call_api.assert_any_await('set_msg_emoji_like', message_id=1, emoji_id='289', set=True)
            bot.call_api.assert_any_await('fetch_emoji_like', message_id=1, emojiId='289', count=20)
        asyncio.run(run())

    def test_runtime_dispatch_and_dedup(self):
        async def run():
            runtime = ParserRuntime()
            try:
                await runtime.start()
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='BV1xx411c7mD')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                parser = runtime.parsers[match[0]]
                parser.parse = AsyncMock(return_value=ParseResult(platform=Platform('bilibili', 'B站'), title='test', url='https://example.com/a'))
                runtime.sender.send_parse_result = AsyncMock()
                runtime.log_skip = Mock()
                await runtime.process(SimpleNamespace(), event, match)
                await runtime.process(SimpleNamespace(), event, match)
                self.assertEqual(parser.parse.await_count, 1)
                self.assertEqual(runtime.sender.send_parse_result.await_count, 1)
                runtime.log_skip.assert_called_once()
                self.assertEqual(runtime.log_skip.call_args.args[0], 'Debounced')
            finally:
                await runtime.close()
        asyncio.run(run())


    def test_send_failure_releases_link_debounce(self):
        async def run():
            runtime = ParserRuntime()
            try:
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='https://xhslink.com/a/example')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                parser = runtime.parsers[match[0]]
                result = ParseResult(platform=Platform('xhs', '小红书'), title='test', url='https://example.com/a')
                parser.parse = AsyncMock(return_value=result)
                runtime.sender.send_parse_result = AsyncMock(side_effect=[RuntimeError('send failed'), None])
                runtime.log_failure = Mock()
                runtime.log_skip = Mock()
                await runtime.process(SimpleNamespace(), event, match)
                await runtime.process(SimpleNamespace(), event, match)
                self.assertEqual(parser.parse.await_count, 2)
                self.assertEqual(runtime.sender.send_parse_result.await_count, 2)
                runtime.log_failure.assert_called_once()
                runtime.log_skip.assert_not_called()
            finally:
                await runtime.close()
        asyncio.run(run())

    def test_parse_failure_releases_link_debounce(self):
        async def run():
            runtime = ParserRuntime()
            try:
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='https://xhslink.com/a/example')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                parser = runtime.parsers[match[0]]
                parser.parse = AsyncMock(side_effect=[
                    RuntimeError('parse failed'),
                    ParseResult(platform=Platform('xhs', '小红书'), title='test', url='https://example.com/a'),
                ])
                runtime.sender.send_parse_result = AsyncMock()
                runtime.log_failure = Mock()
                runtime.log_skip = Mock()
                bot = SimpleNamespace(send=AsyncMock())
                await runtime.process(bot, event, match)
                await runtime.process(bot, event, match)
                self.assertEqual(parser.parse.await_count, 2)
                bot.send.assert_awaited_once()
                runtime.sender.send_parse_result.assert_awaited_once()
                runtime.log_failure.assert_called_once()
                runtime.log_skip.assert_not_called()
            finally:
                await runtime.close()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
