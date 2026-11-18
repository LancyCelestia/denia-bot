"""One serialized business flow: fetch, durable claim, one transport call, receipt."""
import asyncio
import json
import logging
import time

from .model import BiliError, Receipt, Scope, clean, error_text, parse

log=logging.getLogger(__name__)


class Service:
    def __init__(self,store,client,sender,*,clock=time.time,interval=300,targets=None):
        self.store,self.client,self.sender=store,client,sender
        self.clock,self.interval=clock,interval
        self.scan_budget=min(60,interval*0.5)
        self.delivery_budget=min(50,interval*0.4)
        self.targets=targets
        self.lock=asyncio.Lock()
        self.cache={}
        self.last_search={}

    def allowed(self,scope):
        return self.targets is None or scope.key in self.targets

    def _sync_source_stop(self):
        # Cleanup awaits can replace REMOTE_RESTRICTED with a timeout/error.
        # The observed flag is sticky; synchronizing it must not await or mask
        # task cancellation. A failed write latches the ordinary storage stop.
        if getattr(self.client,'restricted',False) and not self.store.source_stopped and not self.store.stopped:
            try:self.store.record_error(None,'REMOTE_RESTRICTED')
            except BiliError:
                self.store.stopped=True
                log.error('bilibili source pause failed: STORAGE_FAULT')

    async def dispatch(self,scope,user,text,*,is_owner=False):
        command=parse(text)
        if command is None:return ''
        action,arg,valid=command
        acquired=False
        result=None;code=None
        try:
            if not valid:raise BiliError('INVALID_ARGUMENT')
            if action in {'subscribe','unsubscribe'} and not is_owner:raise BiliError('NOT_OWNER')
            # 接收目标管的是「往哪里投递」：新建订阅会绑定投递，必须在清单里；
            # 搜索/选择/列表都只是会话内即时回复，不产生投递义务，任何会话可用。
            if action=='subscribe' and not self.allowed(scope):raise BiliError('TARGET_DISABLED')
            await asyncio.wait_for(self.lock.acquire(),1)
            acquired=True
            self.store.require()
            self._sync_source_stop()
            self.store.require()
            result=await asyncio.wait_for(self._command(scope,user,action,arg),180)
        except asyncio.TimeoutError:
            code='DEADLINE' if acquired else 'BUSY'
        except BiliError as exc:
            code=exc.code
            if code=='REMOTE_RESTRICTED' and acquired:
                try:self.store.record_error(arg if action=='subscribe' else '',code)
                except BiliError:code='STORAGE_FAULT'
        except Exception:
            # No remote response, credential, keyword, local path or exception repr in logs.
            code='REMOTE_SCHEMA'
            log.error('bilibili command failed: REMOTE_SCHEMA')
        finally:
            if acquired:
                try:
                    self._sync_source_stop()
                    if self.store.stopped:code='STORAGE_FAULT'
                    elif self.store.source_stopped and action in {'subscribe','search'}:code='REMOTE_RESTRICTED'
                finally:self.lock.release()
        if code is None:return result
        prefix='本次未创建订阅。' if action=='subscribe' else '本次搜索未完成。' if action=='search' else ''
        return prefix+error_text(code)

    async def _command(self,scope,user,action,arg):
        if action=='unsubscribe':
            self.store.remove(scope,arg)
            return '已取消当前会话 UID '+arg+' 的订阅及未发送任务。'
        if action=='list':
            rows=self.store.subscriptions(scope)
            if not rows:return '当前会话暂无订阅。'
            header='来源受限，已暂停。' if self.store.source_stopped else '当前会话订阅：'
            lines=[r['uid']+' '+clean(r['name'],80)+' '+(error_text(r['last_error']) if r['last_error'] else '最近检查：'+str(int(r['last_check'])))+(' 可能遗漏' if r['gap'] else '') for r in rows]
            states=self.store.statuses(scope)
            return header+'\n'+'\n'.join(lines)+'\n待发 '+str(states.get('pending',0)+states.get('retry',0))+'；送达未知 '+str(states.get('unknown',0))+'（不自动重发）；失败 '+str(states.get('failed',0))
        if self.store.source_stopped and action in {'subscribe','search'}:raise BiliError('REMOTE_RESTRICTED')
        if action=='subscribe':
            if any(r['uid']==arg for r in self.store.subscriptions(scope)):
                return '当前会话已订阅 UID '+arg+'。'
            # Capture the boundary before any request. Items at/before it never replay.
            baseline=self.clock()
            name=await self.client.validate_user(arg)
            # A validated recent head establishes the start; old pages need not end.
            page=await self.client.fetch_page(arg)
            self.store.add(scope,arg,name,baseline,page.items)
            return '已订阅 UID '+arg+'（'+clean(name,80)+'）；首次不推历史，新动态发到当前会话。'
        key=(*scope.key,user)
        now=self.clock()
        for old in list(self.cache):
            if self.cache[old][0]<=now:self.cache.pop(old,None)
        for old in list(self.last_search):
            if self.last_search[old]<=now-10:self.last_search.pop(old,None)
        if action=='search':
            if key in self.last_search:raise BiliError('BUSY')
            if len(self.cache)>=1000 or len(self.last_search)>=2048:raise BiliError('BUSY')
            self.last_search[key]=now
            self.cache.pop(key,None)
            rows=await self.client.search(arg)
            self.cache[key]=(self.clock()+120,rows[:5])
            return '没有搜索结果。' if not rows else '\n\n'.join(str(n)+'. '+value for n,value in enumerate(rows[:5],1))+'\n使用 .b站选 1，120秒内有效，不下载。'
        if action=='select':
            if key not in self.cache:raise BiliError('EXPIRED')
            rows=self.cache[key][1]
            number=int(arg)
            return rows[number-1] if number<=len(rows) else '序号越界，请选择1–'+str(len(rows))+'；没有结果时请重新搜索。'
        raise BiliError('INVALID_ARGUMENT')

    async def _scan(self,sub,fetch):
        uid,sub_id=sub['uid'],sub['id']
        now=self.clock()
        state=self.store.scan(uid,sub_id=sub_id)
        if state and state['cursor'] and now-state['started']>=86400:
            self.store.record_error(uid,'SCAN_GAP',sub_id=sub_id)
            state=None
        # Always refresh page 1, then resume a bounded old chain if there is one.
        head=await fetch()
        if state and state['cursor']:
            self.store.ingest(uid,head.items,now,cursor=state['cursor'],started=state['started'],pages=state['pages'],seen=json.loads(state['seen']),sub_id=sub_id)
            cursor=state['cursor'];started=state['started'];pages=state['pages'];seen=json.loads(state['seen'])
            budget=9
        else:
            cursor=head.cursor;started=now;pages=1;seen=[cursor] if cursor else []
            self.store.ingest(uid,head.items,now,cursor=cursor,started=started,pages=pages,seen=seen,sub_id=sub_id)
            budget=9
        for _ in range(budget):
            if cursor is None:return
            if pages>=512:
                self.store.record_error(uid,'SCAN_GAP',sub_id=sub_id);return
            page=await fetch(cursor)
            next_cursor=page.cursor
            if next_cursor is not None and next_cursor in seen:
                self.store.record_error(uid,'SCAN_GAP',sub_id=sub_id);return
            if next_cursor is not None:seen.append(next_cursor)
            pages+=1
            self.store.ingest(uid,page.items,self.clock(),cursor=next_cursor,started=started,pages=pages,seen=seen,sub_id=sub_id)
            cursor=next_cursor

    async def tick(self):
        if self.lock.locked() or self.store.stopped:return
        async with self.lock:
            try:
                self.store.require()
                self._sync_source_stop()
                self.store.require()
                if self.store.source_stopped:return
                # 接收目标之外的历史订阅按休眠处理：不检查、不投递，省下 API 配额。
                rows=[r for r in self.store.subscriptions()
                      if self.allowed(Scope(r['bot'],r['kind'],r['target']))]
                try:
                    await asyncio.wait_for(self._scan_round(rows),self.scan_budget)
                except asyncio.TimeoutError:
                    log.warning('bilibili scan budget reached; checking source pause before delivery')
                self._sync_source_stop()
                self.store.require()
                if self.store.source_stopped:return
                try:
                    await asyncio.wait_for(self._deliver_round(rows),self.delivery_budget)
                except asyncio.TimeoutError:
                    log.warning('bilibili delivery budget reached; unconfirmed call marked unknown')
            except BiliError as exc:
                log.error('bilibili polling paused: %s',exc.code)
            finally:self._sync_source_stop()

    async def _scan_round(self,rows):
        # Rotate both source and target order between cycles; each keeps its quota.
        uids=sorted({r['uid'] for r in rows})
        offset=int(self.clock()//self.interval)%len(uids) if uids else 0
        for uid in uids[offset:]+uids[:offset]:
            cache={};requests=0
            async def fetch(cursor=None):
                nonlocal requests
                if cursor not in cache:
                    if requests>=10:raise BiliError('DEADLINE')
                    requests+=1
                    cache[cursor]=await self.client.fetch_page(uid,cursor)
                return cache[cursor]
            subscribers=[r for r in rows if r['uid']==uid]
            turn=int(self.clock()//self.interval)%len(subscribers)
            for sub in subscribers[turn:]+subscribers[:turn]:
                try:
                    await self._scan(sub,fetch)
                except asyncio.CancelledError:
                    self._sync_source_stop()
                    if not self.store.source_stopped and not self.store.stopped:
                        try:self.store.record_error(uid,'DEADLINE',sub_id=sub['id'])
                        except BiliError:log.error('bilibili scan cancel: STORAGE_FAULT')
                    raise
                except BiliError as exc:
                    self._sync_source_stop()
                    self.store.require()
                    if self.store.source_stopped:return
                    self.store.record_error(uid,exc.code,sub_id=sub['id'])
                    if exc.code in {'REMOTE_RESTRICTED','STORAGE_FAULT'}:return
                except Exception:
                    self._sync_source_stop()
                    self.store.require()
                    if self.store.source_stopped:return
                    raise
                finally:self._sync_source_stop()

    async def _deliver_round(self,rows):
        self._sync_source_stop()
        self.store.require()
        if self.store.source_stopped:return
        guard=getattr(self.client,'require_delivery',None)
        if guard is not None:guard()
        scopes=list(dict.fromkeys(Scope(r['bot'],r['kind'],r['target']) for r in rows))
        offset=int(self.clock()//self.interval)%len(scopes) if scopes else 0
        for scope in scopes[offset:]+scopes[:offset]:
            if not self.allowed(scope) or not self.sender.available(scope):continue
            for _ in range(5):
                self._sync_source_stop()
                self.store.require()
                if guard is not None:guard()
                row=self.store.claim(scope,self.clock(),self.interval)
                if row is None:break
                try:
                    receipt=await asyncio.wait_for(self.sender.send(scope,row['item']),25)
                    if not isinstance(receipt,Receipt) or receipt.kind not in {'sent','unknown','rejected'}:
                        receipt=Receipt('unknown',note='INVALID_RECEIPT')
                    elif receipt.kind=='sent' and not receipt.message_id:
                        receipt=Receipt('unknown',note='MISSING_RECEIPT')
                except asyncio.CancelledError:
                    self.store.complete(row,Receipt('unknown',note='CANCELLED'),self.clock())
                    raise
                except Exception:
                    receipt=Receipt('unknown',note='TRANSPORT_EXCEPTION')
                self.store.complete(row,receipt,self.clock())

