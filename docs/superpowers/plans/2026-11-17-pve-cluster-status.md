# Proxmox VE 集群状态卡 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a superuser-only `查看集群状态` image card that reads Proxmox VE node, guest and storage metrics from the existing Grafana InfluxDB source, with settings in the dashboard's server-status section.

**Architecture:** A new stdlib-only `status_cluster.py` builds fixed Flux queries, normalizes Grafana frames by labels, and formats a view. `status_grafana.py` gains a generic batch query helper. `status_image.py` renders the view with the existing light card style. `plugins/statuscard.py` wires the command and falls back to text. `dashboard.py` / `dashboard.html` expose and test the `cluster` config section.

**Tech Stack:** Python 3.12, NoneBot2, FastAPI dashboard, Grafana `/api/ds/query` (InfluxDB Flux), Playwright renderer, Pillow final scaling, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-pve-cluster-status-design.md`

## Global Constraints

- Reuse `grafana.url` / `grafana.datasource_uid` / `data/status_grafana_secrets.json`; never duplicate the token.
- `status_cluster.py` must not import `nonebot`, `muicebot` or `psutil`; it may import `status_logic` and `status_grafana`.
- Only InfluxDB Flux is supported; no PVE API writes, no alerts, no scheduled cluster pushes.
- Bucket names must match `^[A-Za-z0-9_.-]{1,64}$`; a bad bucket raises a clear error.
- A missing refId or field leaves that section empty; it must never fabricate `0` values.
- The command is superuser-only and uses a separate cooldown key `cluster:<session>`.
- Preserve AGPL file headers on modified statuscard-derived files.
- Do not touch unrelated worktree changes (dashboard meme feature, model files, etc.).

## Review Focus

- Grafana frame labels: every record must merge `schema.fields[*].labels`; losing labels merges nodes/guests incorrectly.
- Multi-node / multi-guest grouping: `host` means node for node metrics, guest name for guest metrics; `nodename` is the node for guests.
- Partial data: a node may have CPU but no memory, a guest may lack `name` or `uptime`; the card shows `--`, never zero.
- Flux injection: bucket is interpolated into a query string; validate before building queries.
- Command failure: Grafana errors must become readable text, and rendering errors must fall back to a summary without crashing the matcher.

---

### Task 1: Data layer and config

**Files:**
- Create: `nonebot/status_cluster.py`
- Modify: `nonebot/status_config.py`
- Modify: `nonebot/status_grafana.py`
- Test: `nonebot/tests/test_status_cluster.py`

**Interfaces:**
- Consumes: `status_logic.format_bytes`, `format_duration`, `format_pct`, `percent_level`; `status_grafana._base_url`, `load_secret`, `GrafanaError`.
- Produces:
  - `status_grafana.query_flux_batch(config: dict, queries: dict[str, str], *, token: str = '', root=None, timeout: int | None = None) -> dict[str, list[dict]]`
  - `status_cluster.build_queries(bucket: str, window_minutes: int, *, include_temperature: bool = True) -> dict[str, str]`
  - `status_cluster.normalize(results: dict[str, list[dict]], *, node_filter: Sequence[str] = ()) -> dict`
  - `status_cluster.build_view(raw: dict, *, title: str = 'Proxmox VE', source: str = 'Grafana', now: float | None = None) -> dict`
  - `status_cluster.fetch(cluster_config: dict, grafana_config: dict, *, root=None, token: str = '', now: float | None = None, use_cache: bool = True) -> dict`
  - `status_cluster.summary_text(view: dict) -> str`
  - `status_cluster.clear_cache() -> None`
  - `status_config.DEFAULT_CONFIG['cluster']` with `enabled=False`, `title='Proxmox VE'`, `bucket='proxmox'`, `datasource_uid=''`, `window_minutes=10`, `node_filter=[]`, `show_temperature=True`, `cache_seconds=15`.

- [ ] **Step 1: Write failing tests** for bucket validation, frame-label merging, multi-node/guest grouping, node filter, missing fields, view formatting, summary text and config whitelist.
- [ ] **Step 2: Implement `query_flux_batch`** using one POST to `/api/ds/query`, returning `{refId: [records]}` where each record merges frame field names and labels.
- [ ] **Step 3: Implement `status_cluster`** with the signatures above; normalize node/guest/storage records and format the view.
- [ ] **Step 4: Add the `cluster` config section** to defaults, `EDITABLE` and `public_view`.
- [ ] **Step 5: Run** `python -m pytest nonebot/tests/test_status_cluster.py nonebot/tests/test_status_grafana.py -q` and fix only data-layer failures.

### Task 2: Card renderer

**Files:**
- Modify: `nonebot/status_image.py`
- Test: `nonebot/tests/test_status_image.py`

**Interfaces:**
- Consumes: the view shape from Task 1 (`title`, `generated_at`, `source`, `summary`, `nodes`, `guests`, `storages`).
- Produces:
  - `status_image.build_cluster_html(view: dict) -> str`
  - `status_image.render_cluster_card_pages(view: dict, *, width: int = 1170) -> list[bytes]`
  - `status_image.render_cluster_card(view: dict, *, width: int = 1170) -> bytes`

- [ ] **Step 1: Write failing renderer tests** for PNG output, requested width, width clamping, empty guests/storages and missing optional fields.
- [ ] **Step 2: Implement `build_cluster_html`** with a compact header, node resource rings, guest tables chunked at 12 rows, storage rows and footer; reuse `STYLE` plus a small cluster CSS block.
- [ ] **Step 3: Implement page/single renderers** via `render_document` + `scale_pages` / `single_page`.
- [ ] **Step 4: Run** `python -m pytest nonebot/tests/test_status_image.py -q` once the shared renderer is available.

### Task 3: Dashboard settings

**Files:**
- Modify: `nonebot/dashboard.py`
- Modify: `nonebot/dashboard.html`
- Test: `nonebot/tests/test_status_panel.py`

**Interfaces:**
- Consumes: `status_cluster.fetch` and `status_config.public_view` / `apply_patch` from Task 1.
- Produces:
  - `POST /api/status/cluster/test` returning `{ok, title, summary, nodes, guests, storages}` or `{ok: false, error}`.
  - Dashboard fields: `statusClusterEnabled`, `statusClusterTitle`, `statusClusterBucket`, `statusClusterUid`, `statusClusterWindow`, `statusClusterNodes`, `statusClusterTemp`, `statusClusterResult`.
  - JS functions `loadStatus`/`saveStatusConfig` include `cluster`; new `testStatusCluster`.

- [ ] **Step 1: Write failing panel tests** for cluster defaults in overview, whitelist save and test endpoint success/failure.
- [ ] **Step 2: Add the test endpoint** after `test_status_grafana`, using unsaved panel values and `use_cache=False`.
- [ ] **Step 3: Add the HTML subsection and JS** after the Grafana temperature subsection.
- [ ] **Step 4: Run** `python -m pytest nonebot/tests/test_status_panel.py -q`.

### Task 4: Command wiring

**Files:**
- Modify: `nonebot/plugins/statuscard.py`
- Modify: `nonebot/gate_logic.py`
- Test: `nonebot/tests/test_statuscard_wiring.py`
- Test: `nonebot/tests/test_help_coverage.py`

**Interfaces:**
- Consumes: `status_cluster.fetch` / `summary_text` from Task 1; `status_image.render_cluster_card_pages` / `RenderError` from Task 2.
- Produces:
  - `COMMANDS` includes `查看集群状态`
  - `send_cluster_card(bot, event)`
  - help group `服务器状态` includes `查看集群状态`

- [ ] **Step 1: Write failing wiring tests** for command matching, disabled config message, Grafana failure text fallback and separate cooldown.
- [ ] **Step 2: Add `build_cluster_view` / `render_cluster_cards` / `send_cluster_card`** to the plugin; fetch in a worker thread, render with the display width, fall back to `summary_text`.
- [ ] **Step 3: Register the command and help row** in `COMMANDS`, `gl.register_help_rows` and `HELP_GROUPS`; update the help coverage expected list.
- [ ] **Step 4: Run** `python -m pytest nonebot/tests/test_statuscard_wiring.py nonebot/tests/test_help_coverage.py -q`.

### Task 5: Docs and verification

**Files:**
- Modify: `docs/OPERATIONS.md`
- Modify: `README.md`

- [ ] **Step 1: Document the command, config fields, data source and failure behavior** in `docs/OPERATIONS.md` 8.5; add "集群状态卡" to the README feature row.
- [ ] **Step 2: Run the targeted suite** (`test_status_cluster`, `test_status_grafana`, `test_status_image`, `test_status_panel`, `test_statuscard_wiring`, `test_help_coverage`).
- [ ] **Step 3: Live-check** the real Grafana source from the dev server, render the card, inspect PNG dimensions/content, and verify the dashboard test endpoint.
- [ ] **Step 4: Run `xd://lsp` diagnostics** on every changed Python file and fix any new errors.
