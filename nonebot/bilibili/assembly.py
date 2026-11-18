"""Only original OneBot text commands enter this plugin, before media/LLM."""
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from .model import BiliError, Scope, parse


def event_command(bot,event):
    if not isinstance(event,(GroupMessageEvent,PrivateMessageEvent)) or str(bot.self_id)!=str(event.self_id):
        return None
    parts=list(event.original_message)
    ats=[part for part in parts if part.type=='at']
    if len(ats)>1 or any(str(part.data.get('qq'))!=str(event.self_id) for part in ats):
        return None
    if parts and parts[0].type=='at':
        parts=parts[1:]
        if not parts or parts[0].type!='text':return None
        first=str(parts[0].data.get('text','')).lstrip(' \t')
    elif parts and parts[0].type=='text':
        first=str(parts[0].data.get('text',''))
    else:return None
    if any(part.type!='text' for part in parts):return None
    text=first+''.join(str(part.data.get('text','')) for part in parts[1:])
    if parse(text) is None:return None
    try:
        scope=Scope(str(bot.self_id),'group' if isinstance(event,GroupMessageEvent) else 'private',
            str(event.group_id) if isinstance(event,GroupMessageEvent) else str(event.user_id))
    except BiliError:return None
    return scope,str(event.user_id),text
