"""Model preference selection for the management panel.

The panel deliberately chooses ``configs/models.yml`` entries, not arbitrary
upstream model names: an entry carries the relay URL, key and persona, while a
bare upstream name can only be validated against a live gateway.  The private
``.模型 首选/次选`` command keeps owning that advanced path.

Saving an entry that is already selected keeps the real-model override set by
that command (``primary_model`` / ``secondary_model``); switching entries clears
it so a stale name cannot be sent through another provider.  The state file is
shared with the bot, so the write is atomic and only through
``model_pref_logic.save_state``.
"""
from __future__ import annotations

import time
from pathlib import Path

import model_pref_logic as mp
import model_publication
import persona_settings

STATE_PATH = mp.DEFAULT_PATH
Unpublished = model_publication.UnpublishedConfig


def _models_path(value) -> Path:
    return Path(value) if value is not None else persona_settings.MODELS_PATH


def _state_path(value) -> Path:
    return Path(value) if value is not None else STATE_PATH


def _canonical(value, names, label: str) -> str | None:
    """Trim and match an entry case-insensitively; empty means 'not set'."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是 models.yml 的条目名")
    text = value.strip()
    if not text:
        return None
    table = {str(name).lower(): name for name in names}
    found = table.get(text.lower())
    if found is None:
        raise ValueError(f"没有叫「{text}」的模型条目")
    return found


def _same_entry(left, right) -> bool:
    return bool(left and right and str(left).lower() == str(right).lower())


def view(models_path=None, templates_dir=None, state_path=None) -> dict:
    """Panel snapshot: entries, current state and publication status, no secrets."""
    state_file = _state_path(state_path)
    config = persona_settings.public(
        _models_path(models_path), templates_dir, state_file
    )
    return {
        "models": config["models"],
        "default_model": config["default_model"],
        "active_model": config["active_model"],
        "active_source": config["active_source"],
        "state": mp.load_state(state_file),
        "publication": config["publication"],
        "config_path": config["config_path"],
        "revision": config["revision"],
    }


def save(primary, secondary, *, models_path=None, templates_dir=None,
         state_path=None) -> dict:
    """Validate entries and atomically replace the shared model-preference state.

    ``configs/models.yml`` is never written.  An unpublished configuration is
    rejected before the state file is read for writing so a manual edit cannot
    make the panel point at an entry the bot has not approved.
    """
    state_file = _state_path(state_path)
    config_path = _models_path(models_path)
    model_publication.read_published(config_path)
    config = persona_settings.public(config_path, templates_dir, state_file)
    names = [item["name"] for item in config["models"]]
    primary = _canonical(primary, names, "首选")
    secondary = _canonical(secondary, names, "次选")

    before = mp.load_state(state_file)
    state = mp.new_state()
    state.update(
        primary=primary,
        primary_model=(
            before.get("primary_model")
            if _same_entry(before.get("primary"), primary) else None
        ),
        secondary=secondary,
        secondary_model=(
            before.get("secondary_model")
            if _same_entry(before.get("secondary"), secondary) else None
        ),
        swaps=before.get("swaps") or {},
        updated=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    mp.save_state(state_file, state)

    result = view(config_path, templates_dir, state_file)
    result["saved"] = {
        "primary": primary,
        "secondary": secondary,
        "restart_required": False,
    }
    return result
