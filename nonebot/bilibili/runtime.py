import asyncio
from functools import partial
import logging
import time

from .client import BiliClient
from .auth import parser_login_cookies
from .delivery import OneBotSender
from .model import BiliError, error_text
from .render import render_card
from .service import Service
from .store import Store

log=logging.getLogger(__name__)


class Runtime:
    def __init__(self,config,bots,*,client_factory=None,sender_factory=None):
        self.config,self.bots=config,bots
        self.client_factory=client_factory or partial(BiliClient,
            cookie_provider=parser_login_cookies if config.bili_use_parser_login else None)
        self.sender_factory=sender_factory or (lambda:OneBotSender(bots,render=partial(render_card,font_file=config.bili_font_file),
            avatar=self.client.avatar if self.client is not None else None,
            cover=self.client.cover if self.client is not None else None))
        self.service=None
        self.client=None
        self.task=None
        self.commands=set()
        self.closing=False
        self.start_error='DISABLED'

    async def start(self):
        if self.service is not None or not self.config.bili_enabled:return
        store=None
        try:
            store=Store(self.config.bili_state_file)
            if self.config.bili_resume_source:store.resume_source()
            self.client=self.client_factory()
            self.service=Service(store,self.client,self.sender_factory(),interval=self.config.bili_interval,targets=self.config.targets())
            self.task=asyncio.create_task(self.worker(),name='bilibili-dynamic-poll')
            self.start_error=''
        except Exception:
            self.start_error='STORAGE_FAULT'
            if self.client is not None:await self.client.close()
            self.client=None
            if store is not None:store.close()
            self.service=None
            log.error('bilibili startup failed; plugin disabled, state preserved')

    async def dispatch(self,scope,user,text,*,is_owner):
        if self.closing:return error_text('DISABLED')
        if self.service is None:return error_text(self.start_error)
        task=asyncio.current_task()
        self.commands.add(task)
        try:return await self.service.dispatch(scope,user,text,is_owner=is_owner)
        finally:self.commands.discard(task)

    async def worker(self):
        while True:
            next_poll=time.monotonic()+self.config.bili_interval
            try:
                await asyncio.wait_for(self.service.tick(),120)
            except asyncio.TimeoutError:
                log.warning('bilibili round budget exceeded; pending state retained')
            except BiliError as exc:
                log.error('bilibili polling failed: %s',exc.code)
            except Exception:
                log.error('bilibili polling failed: INTERNAL_ERROR')
            await asyncio.sleep(max(1,next_poll-time.monotonic()))

    async def close(self):
        self.closing=True
        tasks=[task for task in [self.task,*self.commands] if task is not None and task is not asyncio.current_task()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.client is not None:await self.client.close()
        if self.service is not None:self.service.store.close()
        self.service=None;self.client=None;self.task=None
