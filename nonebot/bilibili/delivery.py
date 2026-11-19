import asyncio
import logging
import re

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .model import Receipt
from .render import render_card

log=logging.getLogger(__name__)


class OneBotSender:
    def __init__(self,bots,*,render=render_card,avatar=None,cover=None):
        self.bots,self.render,self.avatar,self.cover=bots,render,avatar,cover

    def _bot(self,scope):
        bot=self.bots().get(scope.bot)
        return bot if bot is not None and str(bot.self_id)==scope.bot else None

    def available(self,scope):return self._bot(scope) is not None

    async def send(self,scope,item):
        bot=self._bot(scope)
        if bot is None:
            # Proven local non-attempt, unlike a remote ActionFailed response.
            return Receipt('rejected',note='BOT_OFFLINE')
        note=''
        face=None
        if self.avatar is not None:
            try:
                face=await self.avatar(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Avatar is decoration: never let it cost the message.
                log.warning('bilibili avatar unavailable; card drawn without it')
        picture=None
        if self.cover is not None and getattr(item,'cover',''):
            try:
                picture=await self.cover(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A missing cover falls back to a text-only card, never a retry.
                log.warning('bilibili video cover unavailable; card drawn without it')
        try:
            png=self.render(item,avatar=face,cover=picture)
            message=Message([MessageSegment.image(png),MessageSegment.text('\n'+item.url)])
        except Exception:
            note='CARD_FALLBACK'
            log.warning('bilibili card failed; sending explicit text fallback')
            message=Message([MessageSegment.text('【卡片生成失败，文字提醒】\n'+item.plain())])
        api='send_group_msg' if scope.kind=='group' else 'send_private_msg'
        key='group_id' if scope.kind=='group' else 'user_id'
        try:
            result=await asyncio.wait_for(bot.call_api(api,**{key:int(scope.target),'message':message}),20)
        except asyncio.CancelledError:
            raise
        except Exception:
            # An error response, timeout or disconnect is not proof of non-delivery.
            return Receipt('unknown',note='TRANSPORT_UNCONFIRMED')
        message_id=result.get('message_id') if isinstance(result,dict) else None
        if type(message_id) not in {str,int} or not re.fullmatch(r'-?[0-9]{1,20}',str(message_id)):
            return Receipt('unknown',note='MISSING_RECEIPT')
        return Receipt('sent',str(message_id),note)
