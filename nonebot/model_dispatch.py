"""Independent background requests with configured primary and fallback."""
from __future__ import annotations

import asyncio
import yaml
from nonebot import logger
import model_pref_logic as mp
import model_publication


class SelectionChanged(RuntimeError):
    pass


class PreSendUnavailable(SelectionChanged):
    pass


class ModelUnavailable(RuntimeError):
    pass


class InvalidModelResponse(RuntimeError):
    pass


def _selection():
    from plugins import model_pref
    state = model_pref.state()
    result = {key: state.get(key) for key in ('primary', 'primary_model', 'secondary', 'secondary_model')}
    #: 没有持久首选时才看原生命令选中的条目。`model_pref.native_name()` 在同一线程里
    #: 一次性读完，救援途中的临时次选不会被当成主人的选择。
    result['native'] = model_pref.native_name() if not result['primary'] else None
    return result


def failover_enabled():
    from plugins import model_pref
    return model_pref._flag()


def _default_name(configs):
    defaults = [name for name, cfg in configs.items() if isinstance(cfg, dict) and cfg.get('default')]
    return defaults[-1] if defaults else next(iter(configs))


def _manager_ready(configs):
    from muicebot.config import get_model_config_manager
    from muicebot.llm import ModelConfig
    try:
        manager = get_model_config_manager()
        table = manager.configs
        if set(table) != set(configs):
            return False
        if manager.default_config is not table[_default_name(configs)]:
            return False
        for name, raw in configs.items():
            if not isinstance(raw, dict):
                return False
            expected = ModelConfig(**raw)
            if table[name].model_dump(exclude={'template'}) != expected.model_dump(exclude={'template'}):
                return False
        return manager.configs is table and set(table) == set(configs)
    except Exception:
        return False


def read_snapshot():
    from muicebot.config import MODELS_CONFIG_PATH
    try:
        first = model_publication.read_published(MODELS_CONFIG_PATH)
    except model_publication.UnpublishedConfig as exc:
        raise SelectionChanged('model configuration is not published') from exc
    selection = _selection()
    configs = yaml.safe_load(first)
    if not isinstance(configs, dict) or not configs:
        raise ModelUnavailable('model configurations unavailable')
    if not _manager_ready(configs):
        raise SelectionChanged('model configuration reload is incomplete')
    return first, selection, configs


def unchanged(raw, selection):
    from muicebot.config import MODELS_CONFIG_PATH
    try:
        return (model_publication.read_published(MODELS_CONFIG_PATH) == raw
                and _selection() == selection and _manager_ready(yaml.safe_load(raw)))
    except model_publication.UnpublishedConfig:
        return False


def _safe_unchanged(raw, selection):
    try:
        return unchanged(raw, selection)
    except Exception:
        return False


def make_client(config):
    from muicebot.llm import load_model
    return load_model(config).client


def _config(configs, name, require_vision):
    from muicebot.llm import ModelConfig
    raw = configs.get(name)
    if not isinstance(raw, dict):
        raise ModelUnavailable('selected model entry unavailable')
    cfg = ModelConfig(**raw)
    if cfg.provider.lower() != 'openai':
        raise ModelUnavailable('background generation supports only OpenAI provider')
    if not cfg.model_name or (require_vision and not cfg.multimodal):
        raise ModelUnavailable('selected model does not support this request')
    return cfg


def _has_output(result):
    if any(getattr(result, key, None) for key in ('output_text', 'text', 'content')):
        return True
    choices = getattr(result, 'choices', None)
    if not isinstance(choices, (list, tuple)):
        raise InvalidModelResponse('invalid response shape')
    unknowns = False
    for choice in choices:
        if getattr(choice, 'text', None) or getattr(getattr(choice, 'delta', None), 'content', None):
            return True
        message = getattr(choice, 'message', None)
        if message is None:
            continue  # 缺 message 的 choices 算「这次没产出」，不是坏形状
        if any(getattr(message, key, None) for key in ('tool_calls', 'refusal', 'reasoning_content', 'audio')):
            return True
        if not hasattr(message, 'content'):
            unknowns = True
            continue
        content = message.content
        if content and (not isinstance(content, str) or content.strip()):
            return True
    # 全部 choices 都没有正文：确认是空响应（可按 model_pref 规则降级），
    # 还是形状认不出来（不能当「没产出」去重发同一份数据）。
    if unknowns:
        raise InvalidModelResponse('invalid response shape')
    return False


async def complete(*, messages, timeout, require_vision=False, before_send=None, **kwargs):
    sent = False
    try:
        raw, selection, configs = read_snapshot()
    except Exception as exc:
        raise PreSendUnavailable('model configuration is unavailable before sending') from exc
    primary = selection.get('primary') or selection.get('native')
    from_native = not selection.get('primary')
    if primary and from_native and primary not in configs:
        # 原生命令选中的条目已经从发布配置里删掉了：不知道主人想要谁，暂停不猜。
        raise PreSendUnavailable('native model selection is no longer published')
    if not primary:
        try:
            primary = _default_name(configs)
        except Exception:
            primary = None
        from_native = False
    if not primary:
        raise PreSendUnavailable('no usable primary selection before sending')
    secondary = selection.get('secondary')
    if secondary and str(secondary).lower() == str(primary).lower() \
            and (selection.get('secondary_model') or None) == (selection.get('primary_model') or None):
        secondary = None  # 同一个模型重跑一次不叫降级
    effective = dict(selection, primary=primary)
    attempts = [(primary, selection.get('primary_model'))]
    if secondary:
        attempts.append((secondary, selection.get('secondary_model')))
    failure = None
    for index, (name, override) in enumerate(attempts):
        if not _safe_unchanged(raw, selection):
            error = SelectionChanged if sent else PreSendUnavailable
            raise error('model selection changed before request')
        if index:
            allowed, _ = mp.need_failover(effective, enabled=failover_enabled(), error=type(failure).__name__ if failure else '', text='')
            if not allowed:
                break
            logger.warning('[model] background request is using configured secondary')
        client = None
        try:
            cfg = _config(configs, name, require_vision)
            client = make_client(cfg)
            create = getattr(getattr(getattr(client, 'chat', None), 'completions', None), 'create', None)
            if not callable(create):
                raise ModelUnavailable('model client is unavailable')
            if not _safe_unchanged(raw, selection):
                error = SelectionChanged if sent else PreSendUnavailable
                raise error('model selection changed before request')
            if not sent and before_send is not None:
                try:
                    permitted = before_send()
                except Exception as exc:
                    raise PreSendUnavailable('failed to reserve job before sending') from exc
                if not permitted:
                    raise PreSendUnavailable('job is no longer pending')
            sent = True
            result = await asyncio.wait_for(create(model=override or cfg.model_name, messages=messages, **kwargs), timeout=timeout)
            if _has_output(result):
                return result
            failure = ModelUnavailable('empty model response')
        except (SelectionChanged, InvalidModelResponse):
            raise
        except Exception as exc:
            failure = exc
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as exc:
                    logger.warning(f'[model] background client close failed: {type(exc).__name__}')
    if failure is not None:
        if not sent:
            raise PreSendUnavailable('model clients unavailable before sending') from failure
        raise failure
    raise ModelUnavailable('model unavailable')
