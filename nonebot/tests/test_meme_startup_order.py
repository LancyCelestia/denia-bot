import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelCompletions, ModelStreamCompletions
from muicebot.muice import Muice
from plugins import meme, model_pref


@pytest.mark.parametrize('order', ['model_then_meme_then_model', 'meme_then_model_then_meme'])
def test_startup_wrappers_do_not_recurse_or_filter_twice(order):
    async def run():
        calls = []
        async def original(_self, *_args, **_kwargs):
            calls.append('stream')
            yield ModelStreamCompletions('正常回复')
        async def original_ask(_self, *_args, **_kwargs):
            calls.append('ask')
            return ModelCompletions(text='正常回复')
        async def direct(_state, make_first, **_kwargs):
            async for item in make_first():
                yield item
        event = object()
        old_wrapped = getattr(model_pref.wrapped_stream, '__wrapped__', None)
        old_ask_wrapped = getattr(model_pref.wrapped_ask, '__wrapped__', None)
        with patch.object(Muice, 'ask_stream', original), patch.object(Muice, 'ask', original_ask), \
             patch.object(model_pref, 'ORIGINAL_STREAM', None), patch.object(model_pref, 'ORIGINAL_ASK', None), patch.object(model_pref, 'ensure_primary'), \
             patch.object(model_pref, 'state', return_value={}), patch.object(model_pref, '_flag', return_value=False), \
             patch.object(model_pref.mp, 'with_rescue', direct), patch.object(meme, 'get_event', return_value=event):
            try:
                if order == 'model_then_meme_then_model':
                    model_pref.install()
                    meme.install_stream_filter()
                    model_pref.install()
                else:
                    meme.install_stream_filter()
                    model_pref.install()
                    meme.install_stream_filter()
                chunks = [item.chunk async for item in Muice.ask_stream(object(), None, None)]
                assert ''.join(chunks) == '正常回复'
                result = await Muice.ask(object(), None, None)
                assert result.text == '正常回复'
                assert calls == ['stream', 'ask']
                for method in (Muice.ask_stream, Muice.ask):
                    count, current, seen = 0, method, set()
                    while current is not None and id(current) not in seen:
                        seen.add(id(current))
                        if getattr(current, '_meme_filter', False):
                            count += 1
                        current = getattr(current, '__wrapped__', None)
                    assert count == 1
            finally:
                model_pref.wrapped_stream.__wrapped__ = old_wrapped
                model_pref.wrapped_ask.__wrapped__ = old_ask_wrapped
    asyncio.run(run())
