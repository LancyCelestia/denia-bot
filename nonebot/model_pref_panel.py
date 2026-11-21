"""Provider and model settings for the management panel.

The panel manages the default ``configs/models.yml`` entry as one OpenAI-
compatible provider: base URL, write-only API key, and a model chosen from the
provider's ``/v1/models`` list.  Other entries (``echo-test`` and anything the
owner keeps for the private ``.模型`` command) stay hidden from the page.

Provider edits rewrite only the ``api_host`` / ``api_key`` lines of the default
entry and publish the complete file through ``model_publication``.  Model
choices keep writing the shared ``data/model_pref.json``; they apply on the next
reply, while provider edits require a bot reload/restart.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from ruamel.yaml import YAML

import model_pref_logic as mp
import model_publication
import persona_settings

STATE_PATH = mp.DEFAULT_PATH
Unpublished = model_publication.UnpublishedConfig
UPSTREAM_TIMEOUT = 10.0
UPSTREAM_MAX_BYTES = 1024 * 1024
UPSTREAM_MAX_MODELS = 500
MAX_KEY_CHARS = 4096
MAX_MODEL_CHARS = 200


class UpstreamError(RuntimeError):
    """The provider could not return a usable model list."""


def _models_path(value) -> Path:
    return Path(value) if value is not None else persona_settings.MODELS_PATH


def _state_path(value) -> Path:
    return Path(value) if value is not None else STATE_PATH


def _provider_entry(config: Mapping) -> dict:
    models = config.get("models") or []
    for item in models:
        if item.get("default"):
            return item
    if not models:
        raise ValueError("configs/models.yml 没有模型条目")
    return models[0]


def _visible_swaps(raw: Mapping, entry: Mapping) -> dict:
    """Only the provider's failover count is shown; entry names stay internal."""
    count = (raw.get("swaps") or {}).get(entry.get("name"))
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return {}
    return {entry.get("model_name") or "默认模型": count}


def _active_model_name(config: Mapping, entry: Mapping, raw: Mapping) -> str:
    if config.get("active_model") == entry.get("name") and raw.get("primary"):
        return str(raw.get("primary_model") or entry.get("model_name") or "")
    return str(entry.get("model_name") or "")


def _base_url(value) -> str:
    if not isinstance(value, str):
        raise ValueError("base URL 必须是文本")
    text = value.strip()
    if not text:
        raise ValueError("base URL 不能为空")
    if len(text) > 2048:
        raise ValueError("base URL 太长")
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("base URL 必须是 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise ValueError("base URL 不能包含账号密码")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL 不能包含查询参数或片段")
    return text.rstrip("/")


def _api_key(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("API Key 必须是文本")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_KEY_CHARS:
        raise ValueError("API Key 太长")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("API Key 不能包含控制字符")
    return text


def _model_name(value, label: str, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{label}模型不能为空")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}模型必须是文本")
    text = value.strip()
    if len(text) > MAX_MODEL_CHARS:
        raise ValueError(f"{label}模型名太长")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError(f"{label}模型名不能包含控制字符")
    return text


def view(models_path=None, templates_dir=None, state_path=None) -> dict:
    """Page-facing provider snapshot: no key, no entry names, no echo-test."""
    state_file = _state_path(state_path)
    config = persona_settings.public(
        _models_path(models_path), templates_dir, state_file
    )
    entry = _provider_entry(config)
    raw = mp.load_state(state_file)
    return {
        "provider": {
            "base_url": str(entry.get("api_host") or ""),
            "key_configured": bool(entry.get("api_key_configured")),
            "model_name": str(entry.get("model_name") or ""),
            "template": entry.get("template"),
        },
        "state": {
            "primary_model": raw.get("primary_model"),
            "secondary_model": raw.get("secondary_model"),
            "swaps": _visible_swaps(raw, entry),
            "updated": raw.get("updated"),
        },
        "active_model": _active_model_name(config, entry, raw),
        "active_source": config.get("active_source"),
        "publication": config.get("publication") or {},
        "config_path": config.get("config_path"),
        "revision": config.get("revision"),
    }


def _set_line(lines: list[str], replacements: dict[int, str], insertions: list[tuple[int, str]],
              node, document, entry: str, field: str, value: str) -> None:
    try:
        line = node.lc.key(field)[0]
    except (AttributeError, KeyError):
        line = None
    if line is not None and line < len(lines):
        old = lines[line]
        indent = re.match(r"[ \t]*", old).group(0)
        replacements[line] = persona_settings._with_eol(
            f"{indent}{field}: {json.dumps(value, ensure_ascii=False)}"
            f"{persona_settings._inline_comment(old)}",
            old,
        )
        return
    try:
        key_line = document.lc.key(entry)[0]
    except (AttributeError, KeyError) as exc:
        raise ValueError("默认模型条目的位置信息不可用") from exc
    later = [document.lc.key(key)[0] for key in document if key != entry]
    end = min((line for line in later if line > key_line), default=len(lines))
    indent = persona_settings._entry_indent(lines, key_line + 1, end)
    eol = "\r\n" if "\r\n" in "".join(lines) else "\n"
    insertions.append((end, f"{indent}{field}: {json.dumps(value, ensure_ascii=False)}{eol}"))


def _rewrite_provider(text: str, entry: str, base_url: str, api_key: str | None) -> str:
    """Replace only the provider's api_host/api_key lines; keep all other bytes."""
    lines = text.splitlines(keepends=True)
    parser = YAML(typ="rt")
    document = parser.load(text)
    if not isinstance(document, Mapping):
        raise ValueError("configs/models.yml 的条目格式无效")
    node = document.get(entry)
    if not isinstance(node, Mapping):
        raise ValueError(f"模型条目 {entry} 格式无效")

    replacements: dict[int, str] = {}
    insertions: list[tuple[int, str]] = []
    _set_line(lines, replacements, insertions, node, document, entry, "api_host", base_url)
    if api_key is not None:
        _set_line(lines, replacements, insertions, node, document, entry, "api_key", api_key)

    for line, value in replacements.items():
        lines[line] = value
    for line, value in sorted(insertions, reverse=True):
        lines.insert(line, value)
    return "".join(lines)


def _expected_provider(data: Mapping, entry: str, base_url: str, api_key: str | None) -> dict:
    expected = copy.deepcopy(dict(data))
    expected[entry]["api_host"] = base_url
    if api_key is not None:
        expected[entry]["api_key"] = api_key
    return expected


def save_provider(base_url, api_key=None, *, models_path=None, templates_dir=None,
                  state_path=None) -> dict:
    """Rewrite the default provider's base URL/key and publish models.yml."""
    base = _base_url(base_url)
    key = _api_key(api_key)
    path = _models_path(models_path)
    text, data = persona_settings._read_models(path)
    entry = persona_settings._default_model(data)
    try:
        persona_settings._require_published(path)
    except persona_settings.Unpublished as exc:
        raise Unpublished(str(exc)) from exc

    rewritten = _rewrite_provider(text, entry, base, key)
    try:
        loaded = yaml.safe_load(rewritten)
    except yaml.YAMLError as exc:
        raise ValueError("提供商配置写入后格式无效，已放弃保存") from exc
    if loaded != _expected_provider(data, entry, base, key):
        raise ValueError("提供商配置写入校验失败，已放弃保存")

    content = rewritten.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    mode = path.stat().st_mode & 0o777
    prepared = persona_settings._atomic_prepare(path.parent, content)
    try:
        model_publication.publish_file(prepared, path, digest)
    finally:
        prepared.unlink(missing_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass

    result = view(path, templates_dir, state_path)
    result["restart_required"] = True
    return result


def fetch_upstream(base_url=None, api_key=None, *, models_path=None,
                   templates_dir=None) -> dict:
    """Ask the OpenAI-compatible provider for model IDs; never echo the key."""
    path = _models_path(models_path)
    config = persona_settings.public(path, templates_dir)
    entry = _provider_entry(config)
    base = _base_url(base_url if base_url not in (None, "") else entry.get("api_host"))
    key = _api_key(api_key)
    if key is None:
        _, data = persona_settings._read_models(path)
        raw_key = data[persona_settings._default_model(data)].get("api_key")
        key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else None
    if not key:
        raise UpstreamError("尚未配置 API Key")

    url = base.rstrip("/") + "/models"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=UPSTREAM_TIMEOUT,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise UpstreamError("无法连接模型提供商") from exc
    if response.status_code != 200:
        raise UpstreamError(f"模型提供商返回 HTTP {response.status_code}")
    if len(response.content) > UPSTREAM_MAX_BYTES:
        raise UpstreamError("模型列表响应过大")
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError("模型提供商返回的不是 JSON") from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        raise UpstreamError("模型提供商返回格式不正确")

    models: list[str] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() not in models:
            models.append(model_id.strip())
    if not models:
        raise UpstreamError("模型提供商没有返回可用模型")
    return {"models": models[:UPSTREAM_MAX_MODELS], "source": base}


def save_models(primary, secondary, *, models_path=None, templates_dir=None,
                state_path=None) -> dict:
    """Save the two model names as overrides on the default provider entry."""
    state_file = _state_path(state_path)
    config = persona_settings.public(
        _models_path(models_path), templates_dir, state_file
    )
    entry = _provider_entry(config)
    primary = _model_name(primary, "首选", required=True)
    secondary = _model_name(secondary, "次选", required=False)

    before = mp.load_state(state_file)
    state = mp.new_state()
    state.update(
        primary=entry["name"],
        primary_model=None if primary == entry.get("model_name") else primary,
        secondary=entry["name"] if secondary else None,
        secondary_model=(
            None if secondary == entry.get("model_name") else secondary
        ) if secondary else None,
        swaps=before.get("swaps") or {},
        updated=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    mp.save_state(state_file, state)

    result = view(_models_path(models_path), templates_dir, state_file)
    result["saved"] = {
        "primary": primary,
        "secondary": secondary,
        "restart_required": False,
    }
    return result
