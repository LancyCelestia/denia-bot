"""Persona template selection for the management panel.

MuiceBot resolves the persona from the ``template`` field of the active entry in
``configs/models.yml``; ``DEFAULT_TEMPLATE`` is only a fallback for entries that
do not declare one.  The dashboard therefore edits that entry (never an unused
variable and never ``configs/templates.yml``, which only supplies variables).

``models.yml`` is published configuration: background requests read it only
while the published digest matches.  Every save here goes through
``model_publication.publish_file`` and refuses to publish a file that a human
changed without approving it first.  The rewrite is line-scoped so API keys,
comments and unrelated settings are kept byte-for-byte.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

import yaml
from ruamel.yaml import YAML

import model_publication
from private_env import get_private

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
MODELS_PATH = ROOT / "configs" / "models.yml"
STATE_PATH = ROOT / "data" / "model_pref.json"

TEMPLATE_SUFFIXES = (".jinja2", ".j2")
SCOPES = ("active", "all")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Unpublished(ValueError):
    """The current models.yml was not approved, so the panel will not publish it."""


def _path(value, fallback: Path) -> Path:
    return Path(value) if value is not None else fallback


# ---------------------------------------------------------------- templates


def list_templates(templates_dir=None) -> list[dict]:
    """Local templates MuiceBot can load by name (``./templates`` search path)."""
    base = _path(templates_dir, TEMPLATES_DIR)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []

    found: dict[str, dict] = {}
    for entry in entries:
        try:
            if not entry.is_file() or entry.suffix not in TEMPLATE_SUFFIXES:
                continue
            name = entry.stem
            if not NAME_PATTERN.fullmatch(name) or ".." in name:
                continue
            stat = entry.stat()
        except OSError:
            continue
        # ``.jinja2`` wins over ``.j2`` when both exist (MuiceBot appends .jinja2).
        if name in found and entry.suffix != ".jinja2":
            continue
        found[name] = {
            "name": name,
            "file": entry.name,
            "path": str(entry.resolve()),
            "size": stat.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        }
    return [found[name] for name in sorted(found)]


def _builtin_template(name: str) -> Path | None:
    """Resolve MuiceBot's packaged ``builtin_templates`` without importing it."""
    try:
        spec = importlib.util.find_spec("muicebot")
    except (ImportError, AttributeError, ValueError):
        return None
    locations = getattr(spec, "submodule_search_locations", None) if spec else None
    if not locations:
        return None
    base = Path(list(locations)[0]) / "builtin_templates"
    for suffix in TEMPLATE_SUFFIXES:
        candidate = base / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def resolve(template: str | None, templates_dir=None) -> dict:
    """Absolute path MuiceBot would load for ``template``, plus what supplied it."""
    result = {"template": template or "", "path": "", "file": "", "exists": False, "builtin": False}
    if not template:
        return result
    name = template.strip()
    if name.endswith(TEMPLATE_SUFFIXES):
        name = name.rsplit(".", 1)[0]
    if not NAME_PATTERN.fullmatch(name) or ".." in name:
        return result

    base = _path(templates_dir, TEMPLATES_DIR)
    try:
        base = base.resolve()
    except OSError:
        return result
    for suffix in TEMPLATE_SUFFIXES:
        candidate = base / f"{name}{suffix}"
        try:
            if candidate.is_file() and candidate.resolve().parent == base:
                return {
                    "template": name,
                    "path": str(candidate.resolve()),
                    "file": candidate.name,
                    "exists": True,
                    "builtin": False,
                }
        except OSError:
            continue
    builtin = _builtin_template(name)
    if builtin is not None:
        return {
            "template": name,
            "path": str(builtin.resolve()),
            "file": builtin.name,
            "exists": True,
            "builtin": True,
        }
    return result


def normalize_template(value, templates_dir=None) -> str:
    """Accept only a template name that exists directly under ``templates/``."""
    if not isinstance(value, str):
        raise ValueError("人格模板必须是文本")
    name = value.strip()
    if name.endswith(TEMPLATE_SUFFIXES):
        name = name.rsplit(".", 1)[0]
    if not name:
        raise ValueError("请选择人格模板")
    if not NAME_PATTERN.fullmatch(name) or ".." in name or "/" in name or "\\" in name:
        raise ValueError("人格模板名称无效")
    available = {item["name"] for item in list_templates(templates_dir)}
    if name not in available:
        raise ValueError(f"人格模板 {name} 不在 templates 目录中")
    return name


# ---------------------------------------------------------------- model config


def _read_models(models_path=None) -> tuple[str, dict]:
    path = _path(models_path, MODELS_PATH)
    # Decode bytes so CRLF checkouts keep their original line endings on save.
    text = path.read_bytes().decode("utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not data:
        raise ValueError("configs/models.yml 为空或格式无效")
    if not all(isinstance(name, str) and name and isinstance(raw, dict) for name, raw in data.items()):
        raise ValueError("configs/models.yml 的条目格式无效")
    return text, data


def _default_model(data: Mapping) -> str:
    defaults = [name for name, raw in data.items() if isinstance(raw, dict) and raw.get("default")]
    return defaults[-1] if defaults else next(iter(data))


def _persisted_primary(state_path=None) -> str | None:
    path = _path(state_path, STATE_PATH)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    primary = raw.get("primary") if isinstance(raw, dict) else None
    return primary.strip() if isinstance(primary, str) and primary.strip() else None


def _active_model(data: Mapping, state_path=None) -> tuple[str, str]:
    """The entry chat currently uses: persisted ``模型 首选``, else the default entry."""
    primary = _persisted_primary(state_path)
    if primary:
        for name in data:
            if name.lower() == primary.lower():
                return name, "model_pref"
    return _default_model(data), "default"


def _declared(raw: Mapping, env_default: str | None) -> tuple[str | None, str]:
    template = raw.get("template")
    if isinstance(template, str) and template.strip():
        return template.strip(), "entry"
    if env_default:
        return env_default, "default_template"
    return None, "unset"


def publication_state(models_path=None) -> dict:
    path = _path(models_path, MODELS_PATH)
    try:
        model_publication.read_published(path)
        return {"published": True, "reason": ""}
    except model_publication.UnpublishedConfig as exc:
        return {"published": False, "reason": str(exc) or "配置尚未发布"}
    except OSError:
        return {"published": False, "reason": "配置不可读"}


def public(models_path=None, templates_dir=None, state_path=None) -> dict:
    text, data = _read_models(models_path)
    path = _path(models_path, MODELS_PATH)
    env_default = (get_private("DEFAULT_TEMPLATE") or "").strip() or None
    default = _default_model(data)
    active, active_source = _active_model(data, state_path)

    models = []
    for name, raw in data.items():
        template, source = _declared(raw, env_default)
        models.append({
            "name": name,
            "default": name == default,
            "active": name == active,
            "template": template,
            "template_source": source,
            "template_mode": raw.get("template_mode") or None,
        })

    active_template = next(item["template"] for item in models if item["active"])
    return {
        "templates": list_templates(templates_dir),
        "models": models,
        "default_model": default,
        "active_model": active,
        "active_source": active_source,
        "default_template": env_default,
        "resolved": resolve(active_template, templates_dir),
        "publication": publication_state(path),
        "config_path": str(path.resolve()),
        "revision": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


# ---------------------------------------------------------------- rewrite


def _with_eol(new_line: str, old_line: str) -> str:
    for ending in ("\r\n", "\n"):
        if old_line.endswith(ending):
            return new_line + ending
    return new_line


def _inline_comment(line: str) -> str:
    match = re.search(r"[ \t]+#.*$", line.rstrip("\r\n"))
    return match.group(0) if match else ""


def _entry_indent(lines: list[str], start: int, end: int) -> str:
    for line in lines[start:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"[ \t]+", line)
        if match:
            return match.group(0)
    return "  "


def _rewrite_templates(text: str, targets: list[str], template: str) -> str:
    """Replace/insert only ``template:`` lines, keeping every other byte intact."""
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    parser = YAML(typ="rt")
    document = parser.load(text)
    if not isinstance(document, Mapping):
        raise ValueError("configs/models.yml 的条目格式无效")

    replacements: dict[int, str] = {}
    insertions: list[tuple[int, str]] = []

    for name in targets:
        node = document.get(name)
        if not isinstance(node, Mapping):
            raise ValueError(f"模型条目 {name} 格式无效")
        template_line = None
        try:
            template_line = node.lc.key("template")[0]
        except (AttributeError, KeyError):
            template_line = None

        if template_line is not None and template_line < len(lines):
            old = lines[template_line]
            indent = re.match(r"[ \t]*", old).group(0)
            replacements[template_line] = _with_eol(
                f"{indent}template: {template}{_inline_comment(old)}", old
            )
            continue

        try:
            key_line = document.lc.key(name)[0]
        except (AttributeError, KeyError) as exc:
            raise ValueError(f"模型条目 {name} 的位置信息不可用") from exc
        later = [document.lc.key(key)[0] for key in document if key != name]
        end = min((line for line in later if line > key_line), default=len(lines))
        indent = _entry_indent(lines, key_line + 1, end)
        insertions.append((key_line + 1, f"{indent}template: {template}{eol}"))

    for line, value in replacements.items():
        lines[line] = value
    for line, value in sorted(insertions, reverse=True):
        lines.insert(line, value)
    return "".join(lines)


def _expected(data: Mapping, targets: list[str], template: str) -> dict:
    expected = copy.deepcopy(dict(data))
    for name in targets:
        expected[name]["template"] = template
    return expected


def _atomic_prepare(directory: Path, content: bytes) -> Path:
    fd, temporary = tempfile.mkstemp(prefix=".persona-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def save(template, scope="active", *, models_path=None, templates_dir=None, state_path=None) -> dict:
    """Validate and publish a persona change; return the refreshed panel view."""
    name = normalize_template(template, templates_dir)
    if scope not in SCOPES:
        raise ValueError("应用范围无效")

    path = _path(models_path, MODELS_PATH)
    text, data = _read_models(path)
    default = _default_model(data)
    active, _ = _active_model(data, state_path)
    targets = list(data) if scope == "all" else [active]
    if scope == "active" and default != active:
        targets.append(default)

    state = publication_state(path)
    if not state["published"]:
        raise Unpublished(
            "configs/models.yml 当前未发布（缺少或过期摘要），请先核对文件并用 "
            "python -m model_publication --approve-existing 重新发布"
        )

    rewritten = _rewrite_templates(text, targets, name)
    try:
        loaded = yaml.safe_load(rewritten)
    except yaml.YAMLError as exc:
        raise ValueError("人格模板写入后配置格式无效，已放弃保存") from exc
    if loaded != _expected(data, targets, name):
        raise ValueError("人格模板写入校验失败，已放弃保存")

    content = rewritten.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    mode = path.stat().st_mode & 0o777
    prepared = _atomic_prepare(path.parent, content)
    try:
        model_publication.publish_file(prepared, path, digest)
    finally:
        prepared.unlink(missing_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass

    view = public(path, templates_dir, state_path)
    view.update({
        "changed": targets,
        "scope": scope,
        "revision": digest,
        "restart_required": True,
    })
    return view
