# Provider Model Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace entry-name selectors with one provider form (base URL + write-only API key), an upstream model list, and primary/secondary model selectors, while keeping failover and hiding `relay`/`echo-test` from the page.

**Architecture:** `model_pref_panel` grows provider/upstream/model operations. Provider saves use the existing line-scoped `models.yml` rewrite and `model_publication`; model saves keep writing `data/model_pref.json`. The dashboard adds two provider endpoints, and the existing `#model-pref` page is reworked around a provider form plus two model selects.

**Tech Stack:** Python 3.12, FastAPI, httpx, ruamel.yaml, pytest, existing dashboard HTML/JS.

**Spec:** `docs/superpowers/specs/2026-11-21-provider-model-settings-design.md`

## Global Constraints

- Only the default `models.yml` entry is the panel provider; `echo-test` and other entries stay hidden from the page.
- API key is write-only: never returned, logged, or placed in a URL. Blank key keeps the existing key.
- Provider saves rewrite only `api_host` and `api_key` lines of the default entry; comments, other entries, persona, stream and multimodal bytes stay intact.
- Provider saves require a published `models.yml` and return `restart_required: true`; model saves return `restart_required: false`.
- Model saves use `model_pref_logic.save_state`, preserve `swaps`, and set the provider entry plus `primary_model`/`secondary_model`.
- Upstream fetch: http/https only, no URL userinfo, 10s timeout, no redirects, 1 MiB response cap, 500 model cap, Bearer header only.
- Page and API never display `relay` or `echo-test` entry names.
- Follow the existing dashboard layout contract; the page stays `#model-pref` under Bot 基本情况.

## Review Focus

- **Key leakage:** provider GET, upstream errors, page HTML, and logs must not contain the key; blank key must keep the stored key.
- **models.yml integrity:** only the two provider lines change; publication failure leaves the old file and marker intact.
- **Restart boundary:** provider save must flag restart; model save must not.
- **Hidden entries:** the page and API must not expose `relay`/`echo-test` names even though the state file still uses the provider entry internally.
- **Stale/unsafe model names:** reject empty/control-character/overlong names; the upstream list is the UI source, but the API still validates basic shape.
- **Upstream failure:** timeout/non-200/bad JSON/oversize responses leave the form and stored config untouched.

---

### Task 1: Provider view, rewrite and save

**Files:**
- Modify: `nonebot/persona_settings.py` (add `api_host`, `api_key_configured` to each model item in `public`)
- Modify: `nonebot/model_pref_panel.py` (provider view, base URL validation, line rewrite, publish, save)
- Test: `nonebot/tests/test_provider_settings.py`

**Interfaces:**
- Produces:
  - `model_pref_panel.provider_view(models_path=None, templates_dir=None, state_path=None) -> dict`
  - `model_pref_panel.save_provider(base_url, api_key=None, *, models_path=None, templates_dir=None, state_path=None) -> dict`
  - `model_pref_panel.UpstreamError`
  - `model_pref_panel.Unpublished` (alias of `model_publication.UnpublishedConfig`)
- `provider_view` returns `provider: {base_url, key_configured, model_name, template}`, `state`, `active_model`, `active_source`, `publication`; no key and no entry names.

- [ ] **Step 1: Write failing tests**

In `nonebot/tests/test_provider_settings.py`, create a published two-entry `models.yml` (`relay` default, `echo-test` hidden), a temp state, and tests:
- `test_provider_view_hides_entries_and_key`
- `test_save_provider_replaces_base_url_and_key_without_touching_other_bytes`
- `test_save_provider_blank_key_keeps_existing`
- `test_save_provider_rejects_bad_url`
- `test_save_provider_refuses_unpublished`
- `test_save_provider_returns_restart_required`

The integrity test asserts the rewritten file still contains `echo-test`, its comment, `template: Denia`, and `model_name: gemini-3.8-flash-high`, and that the published digest matches.

- [ ] **Step 2: Run tests and confirm failure**

Run: `cd nonebot && python -m pytest tests/test_provider_settings.py -q`
Expected: FAIL on missing provider functions.

- [ ] **Step 3: Add model metadata**

In `persona_settings.public` model items add:

```python
"api_host": str(raw.get("api_host") or ""),
"api_key_configured": bool(raw.get("api_key")),
```

- [ ] **Step 4: Implement provider view and save**

In `model_pref_panel.py`:
- `_base_url(value)` trims, parses with `urlsplit`, requires http/https, host, no username/password/query/fragment; strips trailing `/`.
- `provider_view` uses `persona_settings.public`, selects the default entry, and computes `active_model` as `state.primary_model` when the primary entry is the provider, else the entry's `model_name`.
- `save_provider` reads `persona_settings._read_models`, requires publication, rewrites the default entry's `api_host` and (when provided) `api_key` lines, writes a prepared file, and calls `model_publication.publish_file`. Return the view with `restart_required: True`.
- The line rewrite uses the `ruamel.yaml` line positions, `_entry_indent`, `_inline_comment`, `_with_eol`, and `json.dumps` for YAML scalar quoting; insert a missing line after the entry's last existing field.
- Key: strip, reject control characters, max 4096 chars; blank/None means keep; never include it in errors.

- [ ] **Step 5: Run tests**

Run: `cd nonebot && python -m pytest tests/test_provider_settings.py tests/test_persona_settings.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nonebot/persona_settings.py nonebot/model_pref_panel.py nonebot/tests/test_provider_settings.py
git commit -m "feat(panel): add provider base URL and key settings"
```

### Task 2: Upstream list and model selection

**Files:**
- Modify: `nonebot/model_pref_panel.py`
- Test: `nonebot/tests/test_provider_settings.py`

**Interfaces:**
- Produces:
  - `model_pref_panel.fetch_upstream(base_url=None, api_key=None, *, models_path=None) -> dict`
  - `model_pref_panel.save_models(primary, secondary, *, models_path=None, templates_dir=None, state_path=None) -> dict`

- [ ] **Step 1: Write failing tests**

Add tests:
- `test_upstream_uses_stored_key_and_returns_ids`
- `test_upstream_does_not_follow_redirects`
- `test_upstream_rejects_non_200_bad_json_and_oversize`
- `test_upstream_error_never_contains_key`
- `test_save_models_writes_primary_secondary_and_keeps_swaps`
- `test_save_models_clears_secondary_and_canonicalizes_default_model`
- `test_save_models_rejects_empty_or_control_names`

Mock `httpx.get` / a fake response. Assert no key appears in `str(result)` or error messages.

- [ ] **Step 2: Implement fetch_upstream**

Use `httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10, follow_redirects=False)`; map connection errors, non-200, invalid JSON, oversize (`len(response.content) > 1_048_576`), and empty/invalid `data` to `UpstreamError` with safe messages. Return `{"models": ids[:500], "source": base}`.

- [ ] **Step 3: Implement save_models**

Validate primary (required) and secondary (optional) as stripped strings without control chars, max 200. Read the provider entry and state; set `primary=entry`, `primary_model=None if primary == provider model_name else primary`, `secondary=entry`/`None`, `secondary_model` analogously, preserve `swaps`, refresh `updated`; call `mp.save_state`; return the view plus `restart_required: False`.

- [ ] **Step 4: Run tests**

Run: `cd nonebot && python -m pytest tests/test_provider_settings.py tests/test_model_pref_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nonebot/model_pref_panel.py nonebot/tests/test_provider_settings.py
git commit -m "feat(panel): fetch upstream models and save model choices"
```

### Task 3: Dashboard routes

**Files:**
- Modify: `nonebot/dashboard.py`
- Modify: `nonebot/tests/test_model_pref_panel.py`

**Interfaces:**
- `GET /api/model-pref` → provider view
- `POST /api/model-pref/provider` → provider save
- `POST /api/model-pref/upstream` → upstream list
- `POST /api/model-pref` → model save

- [ ] **Step 1: Update API tests**

Rewrite `test_model_pref_panel.py` for the new response/body shapes, covering login, CSRF, provider save, upstream success/error, model save, no key leakage, no `relay`/`echo-test` in the page-facing response, and 409 on unpublished provider save.

- [ ] **Step 2: Add routes**

Add the provider and upstream POST routes next to the existing model-pref routes; reuse `authorized`/`mutate`; map `Unpublished` 409, `ValueError` 400, `UpstreamError` 502, `OSError` 503. The existing POST body must now require both `primary` and `secondary` model names.

- [ ] **Step 3: Run tests**

Run: `cd nonebot && python -m pytest tests/test_model_pref_panel.py tests/test_dashboard.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add nonebot/dashboard.py nonebot/tests/test_model_pref_panel.py
git commit -m "feat(panel): add provider and upstream model endpoints"
```

### Task 4: Page rework

**Files:**
- Modify: `nonebot/dashboard.html`
- Modify: `nonebot/tests/test_dashboard_layout.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes the four endpoints; keeps `#model-pref` and `loadModelPref`.

- [ ] **Step 1: Rework the card**

Replace the entry dropdowns with:
- base URL input `#modelBaseUrl`;
- password key input `#modelApiKey` with placeholder “已配置，留空保持不变”;
- `#modelProviderSave` and `#modelUpstreamFetch` buttons;
- `#modelPrimary`/`#modelSecondary` model selects;
- `#modelSave`, current/swaps/publication/restart hints.

- [ ] **Step 2: Rework JS**

Implement `applyModelPref`, `loadModelPref`, `fetchModelUpstream`, `saveModelProvider`, `saveModelPref`, `fillModelSelects`. On load, fetch the upstream list when `key_configured`; never fill the key input. Provider save shows the restart hint/button; model save says next reply. Preserve the stale current model as a labeled option. Add the upstream fetch to the page loader; no entry names in text.

- [ ] **Step 3: Update layout test and changelog**

Update `test_dashboard_layout.py` to assert the provider form, model selects, fetch/save hooks, and absence of `relay`/`echo-test` text in the card. Add a CHANGELOG entry.

- [ ] **Step 4: Run checks**

Run: `cd nonebot && python -m pytest tests/test_dashboard_layout.py tests/test_bilibili_panel.py tests/test_model_pref_panel.py -q`
Run the existing inline-script `node --check` extraction and duplicate-id check.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nonebot/dashboard.html nonebot/tests/test_dashboard_layout.py CHANGELOG.md
git commit -m "feat(panel): configure provider and pick upstream models"
```

### Task 5: Final verification and delivery

- [ ] Run the related panel pytest set (dashboard, theme, appearance, bilibili, epic, game, persona, version, layout, provider/model tests).
- [ ] Run `python tests/test_model_pref_wiring.py` and `python tests/test_model_pref_logic.py`.
- [ ] Browser smoke: provider form, fetch list, save provider (restart hint), save primary/secondary, hidden echo-test, 390px no overflow.
- [ ] `git diff --check`; open PR from `feature/provider-model-settings` to `main`; trigger the bot reviewer; after merge deploy and verify a real reply.
