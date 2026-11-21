# Model Settings Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a management-panel page for choosing the chat model's primary and secondary `models.yml` entries, with the bot picking up the change on its next reply.

**Architecture:** A small `model_pref_panel` module reads the published model list and `data/model_pref.json`, validates entries, preserves real-model overrides when an entry is unchanged, and atomically saves the state. A file token in `plugins/model_pref.state()` makes the bot reload that state after the panel writes it. The dashboard adds two routes and one Bot-basics subpage.

**Tech Stack:** Python 3.12, FastAPI, pytest, `nonebot/model_pref_logic`, `nonebot/persona_settings`, plain HTML/CSS/JS dashboard.

**Spec:** `docs/superpowers/specs/2026-11-21-model-settings-panel-design.md`

## Global Constraints

- The panel only accepts entries from the current `configs/models.yml`; no arbitrary upstream model names.
- Never write `configs/models.yml`, API keys, relay URLs, or templates from this page.
- A POST must fail with 409 when `configs/models.yml` is not published; state must remain untouched.
- Preserve `primary_model` / `secondary_model` only when the corresponding entry is unchanged (case-insensitive).
- State writes go through `model_pref_logic.save_state`; no ad-hoc JSON writes.
- Do not restart the bot for a panel save. The next reply must use the new state.
- Follow `docs/agents/dashboard-layout.md`: one full-width card, page head owns the title, no second layout convention.

## Review Focus

- **Unchanged entry vs. real-model override:** saving the same entry must not silently drop a `.模型` override; switching entries must clear it.
- **External state write:** a panel save while the bot is running must be observed by the next `state()` call, including when only swaps or the timestamp changed.
- **Unpublished `models.yml`:** GET may display the entries, but POST must refuse and leave the state byte-identical.
- **Stale state entry:** if the saved primary no longer exists in `models.yml`, the page must say so instead of crashing; the bot falls back to the default.
- **Secret leakage:** the new API response must not contain `api_key` or any other private config field.
- **Navigation drift:** the new subpage must be the only new nav/card pair and must not disturb the existing order.

---

### Task 1: Panel model-preference module and entry metadata

**Files:**
- Create: `nonebot/model_pref_panel.py`
- Modify: `nonebot/persona_settings.py` (model entry dict in `public`)
- Test: `nonebot/tests/test_model_pref_panel.py`

**Interfaces:**
- Consumes: `persona_settings.public(models_path, templates_dir, state_path) -> dict`, `model_pref_logic.load_state(path) -> dict`, `save_state(path, state) -> None`, `model_publication.read_published(path) -> bytes`.
- Produces:
  - `model_pref_panel.STATE_PATH = model_pref_logic.DEFAULT_PATH`
  - `model_pref_panel.Unpublished = model_publication.UnpublishedConfig`
  - `model_pref_panel.view(models_path=None, templates_dir=None, state_path=None) -> dict`
  - `model_pref_panel.save(primary, secondary, *, models_path=None, templates_dir=None, state_path=None) -> dict`
  - `persona_settings.public()["models"][i]` also contains `model_name`, `provider`, `stream`.

- [ ] **Step 1: Write the failing API tests**

In `nonebot/tests/test_model_pref_panel.py`, use a fixture like `test_persona_dashboard.py` with a published two-entry `models.yml` (`relay` default, `echo-test`), an auth file, and a temp state path. Include tests named:
- `test_model_pref_requires_login`
- `test_model_pref_post_requires_csrf`
- `test_model_pref_rejects_unknown_entry_and_keeps_state`
- `test_model_pref_preserves_override_for_unchanged_entry`
- `test_model_pref_clears_override_when_entry_changes`
- `test_model_pref_clears_secondary_and_swaps_are_preserved`
- `test_model_pref_refuses_unpublished_models`
- `test_model_pref_response_contains_no_api_key`

Example assertions for the preservation test:

```python
before = {"primary": "relay", "primary_model": "upstream-a",
          "secondary": "echo-test", "secondary_model": "upstream-b",
          "swaps": {"relay": 2}, "updated": "2026-11-20 10:00:00"}
mp.save_state(state_path, mp.new_state() | before)
response = client.post("/api/model-pref", json={"primary": "relay", "secondary": "echo-test"}, headers=headers)
assert response.status_code == 200
state = mp.load_state(state_path)
assert state["primary_model"] == "upstream-a"
assert state["secondary_model"] == "upstream-b"
assert state["swaps"] == {"relay": 2}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd nonebot && .venv\Scripts\python.exe -m pytest tests/test_model_pref_panel.py -q`
Expected: FAIL on the missing `/api/model-pref` route.

- [ ] **Step 3: Add entry metadata to `persona_settings.public`**

In the `models.append({...})` dict, add:

```python
"model_name": str(raw.get("model_name") or ""),
"provider": str(raw.get("provider") or ""),
"stream": bool(raw.get("stream", True)),
```

- [ ] **Step 4: Implement `nonebot/model_pref_panel.py`**

Implement `view` and `save` per the spec. `view` returns only `models`, `default_model`, `active_model`, `active_source`, `state`, `publication`, `config_path`, and `revision`. `save`:

```python
model_publication.read_published(config_path)
config = persona_settings.public(config_path, templates_dir, state_file)
names = [item["name"] for item in config["models"]]
primary = _canonical(primary, names, "首选")
secondary = _canonical(secondary, names, "次选")
before = mp.load_state(state_file)
state = mp.new_state()
state.update(
    primary=primary,
    primary_model=before.get("primary_model") if _same(before.get("primary"), primary) else None,
    secondary=secondary,
    secondary_model=before.get("secondary_model") if _same(before.get("secondary"), secondary) else None,
    swaps=before.get("swaps") or {},
    updated=time.strftime("%Y-%m-%d %H:%M:%S"),
)
mp.save_state(state_file, state)
result = view(config_path, templates_dir, state_file)
result["saved"] = {"primary": primary, "secondary": secondary, "restart_required": False}
return result
```

`_canonical` accepts `None`, `""`, and whitespace as clear; rejects non-strings and unknown entries with `ValueError`.

- [ ] **Step 5: Add the dashboard routes**

In `nonebot/dashboard.py`, import `model_pref_panel` and add the GET/POST routes near `/api/persona`. GET catches `(OSError, ValueError, KeyError)` as 503. POST calls `mutate(req)`, rejects a non-dict body with 400, maps `model_pref_panel.Unpublished` to 409, `(ValueError, TypeError)` to 400, and `OSError` to 503.

- [ ] **Step 6: Run the tests and pass**

Run: `cd nonebot && .venv\Scripts\python.exe -m pytest tests/test_model_pref_panel.py tests/test_persona_dashboard.py tests/test_persona_settings.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nonebot/model_pref_panel.py nonebot/persona_settings.py nonebot/dashboard.py nonebot/tests/test_model_pref_panel.py
git commit -m "feat(panel): add model primary/secondary settings API"
```

### Task 2: Bot reloads the state written by the panel

**Files:**
- Modify: `nonebot/plugins/model_pref.py` (`_state_cache`, `state`, `_persist`)
- Modify: `nonebot/tests/test_model_pref_wiring.py` (`use_temp_state` reset; new `t_` test)

**Interfaces:**
- Consumes: `STATE_PATH`, `mp.load_state`, `mp.save_state`.
- Produces: `state()` returns the on-disk state when the file token changes; `_persist()` refreshes the token.

- [ ] **Step 1: Write the failing test**

Add to `test_model_pref_wiring.py`:

```python
def t_the_panel_writing_state_is_seen_without_restart():
    path = use_temp_state(primary=None, secondary=None)
    assert pref.state()["primary"] is None
    changed = mp.new_state()
    changed.update(primary="relay", primary_model="upstream-a", secondary="echo-test")
    mp.save_state(path, changed)
    assert pref.state()["primary"] == "relay"
    assert pref.state()["primary_model"] == "upstream-a"
    assert pref.state()["secondary"] == "echo-test"
```

Run: `cd nonebot && .venv\Scripts\python.exe tests\test_model_pref_wiring.py`
Expected: FAIL because `state()` keeps the cached `primary=None`.

- [ ] **Step 2: Implement the token**

Add `_state_token_value: tuple[int, int, int] | None = None` and a helper:

```python
def _state_token() -> tuple[int, int, int] | None:
    try:
        stat = STATE_PATH.stat()
        return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError:
        return None
```

In `state()`, reload when the cache is missing, the day changed, or the token changed. In `_persist()`, update the token after saving. Reset `pref._state_token_value = None` in `use_temp_state()`.

- [ ] **Step 3: Run the tests and pass**

Run: `cd nonebot && .venv\Scripts\python.exe tests\test_model_pref_wiring.py`
Expected: all `t_` tests pass.

- [ ] **Step 4: Commit**

```bash
git add nonebot/plugins/model_pref.py nonebot/tests/test_model_pref_wiring.py
git commit -m "fix(model): reload model preference after panel writes"
```

### Task 3: Dashboard subpage and layout contract

**Files:**
- Modify: `nonebot/dashboard.html` (nav, section, `sectionLoaders`, `basicsHashSet`, `enterApp`, JS)
- Modify: `nonebot/tests/test_bilibili_panel.py` (basics order)
- Modify: `nonebot/tests/test_dashboard_layout.py` (only if a new assertion is needed)
- Modify: `docs/agents/dashboard-layout.md` (Bot subpage list)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `GET/POST /api/model-pref`; existing `api`, `run`, `parser-grid`, `parser-field`, `parser-actions` classes.
- Produces: nav link `#model-pref`, section `#model-pref.card`, `loadModelPref()`, `saveModelPref()`, `applyModelPref(data)`.

- [ ] **Step 1: Add the page structure**

Insert the nav link after `#persona`; insert the card after the persona section. Use two `<select>` elements (`modelPrimary`, `modelSecondary`), a save button (`modelSave`), refresh, and status/detail paragraphs. The card h2 is `模型设置`.

- [ ] **Step 2: Add the JS**

Add `loadModelPref`, `saveModelPref`, `applyModelPref`, and the option/detail helpers. Register `['#model-pref', loadModelPref]` in `sectionLoaders`; add `#model-pref` to the `basicsHashSet` fallback; call `await run(loadModelPref)` in `enterApp`. Disable save when `publication.published` is false. Preserve a stale current entry as a labeled option instead of dropping it.

- [ ] **Step 3: Update tests and contract**

Update `basics_links == ['bot-info', 'persona', 'model-pref', 'tokens', 'master', 'superusers']`. Update the contract line listing Bot subpages. Add a CHANGELOG entry under 未发布.

- [ ] **Step 4: Run structural checks**

Run: `cd nonebot && .venv\Scripts\python.exe -m pytest tests/test_dashboard_layout.py tests/test_bilibili_panel.py tests/test_persona_dashboard.py -q`
Run: `node --check` on the extracted inline script (or the team's existing extraction command).
Expected: PASS, no duplicate IDs, no missing nav/card pair.

- [ ] **Step 5: Browser smoke test**

Start a local dashboard on a spare port against a temp data dir, log in, open `#model-pref`, and verify: two selects render entries with model names; save with unchanged entries preserves the override; switching clears it; unpublished config locks save. Capture the result in the PR.

- [ ] **Step 6: Commit**

```bash
git add nonebot/dashboard.html nonebot/tests/test_bilibili_panel.py nonebot/tests/test_dashboard_layout.py docs/agents/dashboard-layout.md CHANGELOG.md
git commit -m "feat(panel): add model settings subpage"
```

### Task 4: Final verification and delivery

- [ ] Run the relevant pytest set: dashboard, theme, appearance, bilibili, epic, game, persona, version, layout, new model panel.
- [ ] Run `python tests\test_model_pref_wiring.py` and `python tests\test_model_pref_logic.py`.
- [ ] Confirm `git diff --check` and the branch only contains this task.
- [ ] Open the PR from `feature/model-settings-panel` to `main`, include commands/results, layout impact, browser evidence, and known limits.
- [ ] After merge, deploy the dashboard and bot, then verify the production page loads with the current `relay` override and that saving does not restart the bot.
