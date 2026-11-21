"""Lifecycle and dispatch around the upstream parser classes."""

import asyncio
import os
import re
from time import perf_counter

from nonebot import logger

from parser_logic import find_match, message_text, session_key
from .core.arbiter import ArbiterContext, EmojiLikeArbiter
from .core.clean import CacheCleaner
from .core.config import PluginConfig
from .core.debounce import Debouncer
from .core.download import Downloader
from .core.parsers import BaseParser, BilibiliParser
from .core.render import Renderer
from .core.perf import begin, end, outcome, phase, record
from .core.sender import MessageSender
from .core.utils import extract_json_url
from .transport import Transport


class ArbiterBot:
    def __init__(self, bot):
        self.bot = bot

    async def set_msg_emoji_like(self, message_id, emoji_id, set, **kwargs):
        return await asyncio.wait_for(self.bot.call_api('set_msg_emoji_like', message_id=message_id, emoji_id=str(emoji_id), set=set), timeout=5)

    async def fetch_emoji_like(self, message_id, emojiId, count, **kwargs):
        return await asyncio.wait_for(self.bot.call_api('fetch_emoji_like', message_id=message_id, emojiId=emojiId, count=count), timeout=5)


class ParserRuntime:
    def __init__(self):
        self.cfg = PluginConfig()
        paths = [str(p) for p in self.cfg.media_tools_paths if p]
        if paths:
            os.environ['PATH'] = os.pathsep.join([*paths, os.environ.get('PATH', '')])
        self.downloader = Downloader(self.cfg)
        self.renderer = Renderer(self.cfg)
        self.sender = MessageSender(self.cfg, self.renderer)
        self.debouncer = Debouncer(self.cfg)
        self.arbiter = EmojiLikeArbiter()
        self.cleaner = CacheCleaner(self.cfg)
        self.parsers = {}
        self.patterns = []
        self.tasks = set()
        for cls in BaseParser.get_all_subclass():
            if cls.platform.name not in self.cfg.parser.enabled_platforms():
                continue
            parser = cls(self.cfg, self.downloader)
            for keyword, pattern in cls._key_patterns:
                self.parsers[keyword] = parser
                self.patterns.append((keyword, re.compile(pattern) if isinstance(pattern, str) else pattern))
        self.patterns.sort(key=lambda item: -len(item[0]))

    async def start(self):
        await asyncio.to_thread(Renderer.load_resources)
        if not self.cfg.config_path.exists():
            self.cfg.save_config()
        logger.info(f'[parser] 已启用 {len(set(self.parsers.values()))} 个平台')

    def match(self, event):
        session = session_key(event)
        if self.cfg.whitelist and session not in self.cfg.whitelist:
            return None
        if session in self.cfg.blacklist or str(event.user_id) == str(event.self_id):
            return None
        text = message_text(event, extract_json_url, self.cfg.enable_reply_parse)
        return find_match(text, self.patterns) if text else None

    def dispatch(self, bot, event, match):
        task = asyncio.create_task(self.process(bot, event, match))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def process(self, bot, event, match):
        keyword = found = None
        session = ''
        result = None
        started = perf_counter()
        parser = self.parsers.get(match[0])
        token = begin(getattr(getattr(parser, "platform", None), "name", "unknown"))
        status = "ok"
        try:
            keyword, found = match
            session = session_key(event)
            if getattr(event, 'group_id', None) is not None and self.cfg.arbiter:
                ctx = ArbiterContext(int(event.message_id), int(event.time), int(event.self_id))
                if not await self.arbiter.compete(ArbiterBot(bot), ctx):
                    status = "skipped"
                    return
            if self.debouncer.hit_link(session, found.group(0)):
                status = "skipped"
                self.log_skip(
                    'Debounced',
                    f'同一链接在 {getattr(self.debouncer, "interval", 300)} 秒内重复，已跳过',
                    platform=self._platform_name(keyword),
                    url=found.group(0),
                )
                return
            try:
                with phase("parse"):
                    result = await self.parsers[keyword].parse(keyword, found)
            except Exception as exc:
                status = "error"
                platform = self.parsers[keyword].platform.name
                self.log_failure(exc, platform=platform, url=found.group(0))
                logger.warning(f'[parser] {platform} 解析失败：{type(exc).__name__}')
                message = self.failure_message(exc, platform)
                try:
                    await asyncio.wait_for(bot.send(event, message), timeout=15)
                except Exception:
                    logger.warning('[parser] 无法发送解析失败提示')
                self._release_debounce(session, found)
                return
            if self.debouncer.hit_resource(session, result.get_resource_id()):
                status = "skipped"
                self.log_skip(
                    'DuplicateResource',
                    f'同一资源在 {getattr(self.debouncer, "interval", 300)} 秒内重复，已跳过',
                    platform=self._platform_name(keyword),
                    url=getattr(result, 'url', '') or found.group(0),
                )
                return
            with phase("send"):
                await self.sender.send_parse_result(Transport(bot, event, self.cfg), result)
        except asyncio.CancelledError:
            status = "cancelled"
            self._release_debounce(session, found, result)
            raise
        except Exception as exc:
            status = "error"
            logger.exception('[parser] 解析或发送失败')
            platform = ''
            try:
                platform = self.parsers[keyword].platform.name if keyword else ''
            except (KeyError, AttributeError):
                pass
            self.log_failure(exc, platform=platform, url=found.group(0) if found else '')
            self._release_debounce(session, found, result)
        finally:
            try:
                record("total", started, outcome() if status == "ok" else status)
            finally:
                end(token)

    def _platform_name(self, keyword: str | None) -> str:
        parser = self.parsers.get(keyword) if keyword else None
        return getattr(getattr(parser, 'platform', None), 'name', '')

    def log_skip(self, reason: str, detail: str, *, platform: str = '', url: str = '') -> None:
        """把防抖跳过写进事件日志，面板能看到“为什么没有回复”。"""
        try:
            from .core.event_log import event_log
            event_log(self.cfg).record('warn', reason, detail, platform=platform, url=url)
        except Exception:  # noqa: BLE001 - 日志失败不能影响主流程
            logger.debug('[parser] 事件日志写入失败', exc_info=True)

    def _release_debounce(self, session: str, found, result=None) -> None:
        """发送/解析失败后释放防抖，允许同链接立即重试。"""
        if not session or found is None:
            return
        try:
            self.debouncer.release_link(session, found.group(0))
        except Exception:  # noqa: BLE001
            logger.debug('[parser] 链接防抖释放失败', exc_info=True)
        if result is not None:
            try:
                self.debouncer.release_resource(session, result.get_resource_id())
            except Exception:  # noqa: BLE001
                logger.debug('[parser] 资源防抖释放失败', exc_info=True)

    def log_failure(self, exc: Exception, *, platform: str = '', url: str = ''):
        """把失败写进事件日志；超出阈值单独归类，面板上能一眼看出来。"""
        try:
            from .core.exception import DownloadLimitException
            from .core.event_log import event_log

            limit = isinstance(exc, DownloadLimitException)
            detail = getattr(exc, 'detail', '') or str(exc) or type(exc).__name__
            event_log(self.cfg).record(
                'limit' if limit else 'error',
                getattr(exc, 'reason', '') or type(exc).__name__,
                detail,
                platform=platform,
                url=url,
                extra={'type': type(exc).__name__},
            )
        except Exception:  # noqa: BLE001 - 记日志失败不能影响主流程
            logger.debug('[parser] 事件日志写入失败', exc_info=True)

    @staticmethod
    def failure_message(exc: Exception, platform: str) -> str:
        """失败提示。阈值类给出具体数值，其余按平台给通用文案。"""
        from .core.exception import DownloadLimitException, human_size

        if isinstance(exc, DownloadLimitException):
            return f'解析失败：{getattr(exc, "detail", "") or str(exc)}'
        if platform == 'xhs':
            return '小红书解析失败，链接可能失效，或需要配置小红书 Cookie。'
        return '链接解析失败，请检查链接或稍后重试。'

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.downloader.close()
        for parser in set(self.parsers.values()):
            await parser.close_session()
        await self.cleaner.stop()

    def bilibili(self):
        return next((p for p in self.parsers.values() if isinstance(p, BilibiliParser)), None)
