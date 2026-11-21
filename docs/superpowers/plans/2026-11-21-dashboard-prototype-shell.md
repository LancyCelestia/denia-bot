# 管理面板原型外壳实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PR #46 原型的外壳（page-head / hero / card tray / 12 栏概览 / 圣杯 context rail）移植到正式 `nonebot/dashboard.html`，新会话默认 Korean，保留真实面板、JS、API。

**Architecture:** 在现有单文件 `dashboard.html` 后部追加独立 `<style id="prototypeShell">`，用现有 theme token 做布局；在 `#app` 内替换 topline/intro 为 page-head/hero/KPI，包装 sections 为 `#panelGrid`；新增 scroll spy、移动抽屉、context rail 和 KPI/hero 数据绑定，不新增后端端点。

**Tech Stack:** 单文件 HTML/CSS/JS、现有 NoneBot/FastAPI dashboard、Playwright 截图、pytest。

**Spec:** `docs/superpowers/specs/2026-11-21-dashboard-prototype-shell-design.md`

## Global Constraints

- 保留现有 section ID、表单 ID、onclick、`appearanceButton`/`appearanceButtonMobile`、`appMessage`、`basics-nav`、登录/CSRF/API。
- 新会话默认 `korean`；已有 `localStorage` 的显式 skin 不迁移；服务器 `theme_panel.default_config()` 不变。
- 不新增 API、不重构 28 面板 router、不改后端/auth/SnowLuma/构建链。
- 12 栏只用于概览/摘要；长表单/表格保持 span 12；≤1100px 单列。
- 真实数据：`/api/bot-status`、`/api/status/overview`、`/api/token`；没有的指标显示 `—`。
- 1440/1024/390 无 console error、无横向溢出；reduced motion/focus 可见。
- `dashboard.html` 是已压缩长行文件；用精确字符串编辑，避免改写无关行。

## Review Focus

- CSS 特异性：新样式块最后，用 `#app`/`#panelGrid` 作用域，不能被 11 套 skin 的 `.card`/`.sidebar` 规则压掉；逐 skin 截图。
- 12 栏挤压：长表单/表格不得变窄到不可用；1024/1440 截图后把问题 section 改回 span 12。
- 移动抽屉：onclick 锚点仍工作，点击后关闭；Esc/遮罩关闭；无横向溢出。
- 数据失败：`/api/status` 关闭或 `/api/bot-status` 失败时 hero/KPI 显示 `—`/“状态未知”，不抛错、不伪造历史。
- 默认 skin：新 localStorage 得到 Korean；已保存 `skin=default` 仍为 default；服务器 `default` 预设不变。

---

### Task 1: 外壳标记、CSS 与默认 skin

**Files:**
- Modify: `nonebot/dashboard.html`
- Test: 本地 dashboard + 1440×1000 截图

**Interfaces:**
- Produces: `#pageTitle`、`#pageDesc`、`#shellLive`、`#shellLiveText`、`#heroBotName`、`#heroVerdict`、`#heroSub`、`#heroLastRefresh`、`#heroStartedAt`、`#heroHeartbeat`、`#heroLinkPill`、`#heroLinkText`、`#kpiCpu`、`#kpiCpuSmall`、`#kpiMem`、`#kpiMemSmall`、`#kpiDisk`、`#kpiDiskSmall`、`#kpiTokens`、`#kpiTokensSmall`、`#navToggle`、`#navOverlay`、`#contextRail`、`#panelGrid`。

- [ ] **Step 1: 插入 `<style id="prototypeShell">`**

放在 `appearanceTheme` 的 `</style>` 之后、`appearanceBoot` 的 `<script>` 之前。内容：
- `:root` 补充 `--tray:var(--surface-2);--paper-2:var(--surface-3);--font-display:...;--font-body:...;--font-mono:...;`。
- `#app .sidebar` 浮动卡片 264px；`html[data-skin="holy"] #app .sidebar` 240px；`.bot-card/.avatar/.nav-overlay`。
- `.workspace` → 页面容器：`margin-left:264px; max-width:1180px; padding:24px 32px 64px;`；holy 保持 240px + ≥1280 右侧 rail。
- `.page-head`、`.hero`、`.hero-core`、`.hero-main`、`.hero-side`、`.kpis`、`.kpi`、`.pill`、`.dot`、`.btn`、`.live-pill`、`.ruler-plot/.rtick`。
- `#panelGrid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:24px}`；`#panelGrid > .card{margin:0;box-shadow:0 0 0 4px var(--tray),var(--shadow-card)}`；按设计文档的 span。
- `@media(max-width:1100px)` 单列；`@media(max-width:800px)` 抽屉、page-head/hero 堆叠、KPI 2 列。
- `html[data-skin="holy"] .context-rail` ≥1280 显示；≤1279 隐藏。
- `prefers-reduced-motion` 关闭过渡。

- [ ] **Step 2: 替换 `<main class="workspace">` 顶部**

把 `topline` + `intro` 替换为：
- `header.page-head`：`#navToggle`、`#pageTitle`、`#pageDesc`、`#shellLive/#shellLiveText`、`#appearanceButtonMobile`、刷新按钮 `onclick="refreshShell()"`。
- `section.hero`：bot avatar/`#heroBotName`、`#heroVerdict`、`#heroSub`、刷新/服务/日志链接；hero-side 的 `#heroLinkPill/#heroLinkText`、`#heroHbNote`、`#heroHeartbeat` 24 个 `span.rtick`、`#heroLastRefresh`、`#heroStartedAt`。
- `div.kpis` 六个 KPI，使用上述 ID，p95/速率显示 `—`/“暂无数据”。
- 保留 `#appMessage`、`#botBasicsNav` 原样。

- [ ] **Step 3: 包装 sections**

在 `#botBasicsNav` 之后、第一个 `<section id="bot-info"` 之前插入 `<div id="panelGrid" class="panel-grid">`；在最后一个 section 之后、footer 之前插入 `</div>`。section ID/内容不动。

- [ ] **Step 4: sidebar / footer / context rail**

- brand 之后插入 `bot-card`（`#shellAvatar/#shellBotName/#shellBotLive`）。
- footer 改为 `class="page-footer"`，内容保留版本/本地管理；不再只对 holy 显示。
- `</main>` 后插入 `aside.context-rail#contextRail`（当前 panel 标题/描述、链接容器、版本）。
- `#app` 内加 `div#navOverlay`（移动遮罩）。

- [ ] **Step 5: 默认 skin**

`<html ... data-skin="default">` → `data-skin="korean"`；`appearanceBoot` 的 `defaults()` 返回 `skin:'korean'`。服务器 `theme_panel.default_config()` 不变。

- [ ] **Step 6: 视觉检查**

启动本地 dashboard（临时密码），Playwright 1440×1000 截图。Expected：sidebar/page-head/hero/KPI/卡片网格可见，无 console error、无横向溢出。

- [ ] **Step 7: 提交**

`git add nonebot/dashboard.html && git commit -m "feat(dashboard): port prototype shell layout"`

### Task 2: JS 数据绑定、scroll spy、移动抽屉

**Files:**
- Modify: `nonebot/dashboard.html`
- Test: 本地 dashboard + Playwright 1440/1024/390

**Interfaces:**
- Consumes: Task 1 IDs。
- Produces: `refreshShell()`、`initShell()`、`updateShellBotStatus(s)`、`updateShellStatus(x)`、`applyBotName` 扩展。

- [ ] **Step 1: `applyBotName`**

同时更新 `#shellBotName`、`#heroBotName`，头像取名称首字。

- [ ] **Step 2: `loadBotStatus`**

成功时更新 `#shellLive/#shellLiveText`、`#heroVerdict`、`#heroSub`、`#heroLinkPill/#heroLinkText`、`#heroLastRefresh`、`#heroHeartbeat` 的 tick tone；失败时显示“状态未知”，不抛到外层。

- [ ] **Step 3: `loadStatus` / `loadTokens`**

`loadStatus` 末尾调用 `updateShellStatus(x)`：CPU/内存/磁盘/运行时长；`x.enabled===false` 时全部 `—`。`loadTokens` 更新 `#kpiTokens`（`formatTokens(t.today)`）和 small（近 7 天）。不新增 fetch。

- [ ] **Step 4: `initShell()`**

在 `enterApp` 中调用：
- scroll spy 根据 `#panelGrid > section[id]` 更新 `#pageTitle/#pageDesc`、context rail 标题/链接和当前项。
- `#navToggle` 切换 `body.nav-open`；`#navOverlay`/Esc/nav link 点击关闭。
- `#contextRail` 链接从 section 列表生成；只在 holy ≥1280 可见（CSS 控制）。
- `refreshShell()` 调用 `refresh()`、`loadStatus()`、`loadTokens()`、`loadBotStatus()`，用现有 `run()` 捕获错误。

- [ ] **Step 5: 响应式/错误检查**

Playwright 1440×1000、1024×768、390×844：抽屉开关、section 跳转、主题切换、API 失败/关闭状态；无 console error、无横向溢出。

- [ ] **Step 6: 提交**

`git add nonebot/dashboard.html && git commit -m "feat(dashboard): wire prototype shell status and navigation"`

### Task 3: 测试、文档、最终验证与 PR

**Files:**
- Modify: `nonebot/tests/test_dashboard.py`（仅在现有结构适合时加 shell 标记/默认 skin 断言）
- Modify: `CHANGELOG.md`
- Test: pytest + Playwright + GitHub clone/PR

- [ ] **Step 1: 更新 CHANGELOG**

`[未发布]` 新增：管理面板采用 PR #46 原型外壳（page-head/hero/card tray/12 栏/圣杯 rail），新会话默认韩式极简，保留已保存主题。

- [ ] **Step 2: focused pytest**

`python -m pytest nonebot/tests/test_dashboard.py nonebot/tests/test_theme_panel.py nonebot/tests/test_appearance_panel.py -q`。Expected：全绿；若现有 HTML 文本断言因外壳改动失败，按新结构更新断言。

- [ ] **Step 3: 最终截图矩阵**

1440×1000（Korean/holy/default）、1024×768、390×844；检查 page-head/hero/KPI/panel grid/context rail/drawer；0 console error、0 横向溢出。将截图路径写入报告。

- [ ] **Step 4: 推送、PR**

`git push -u origin feature/dashboard-prototype-shell`；`gh pr create --base main ...`，body 写变更、验证、已知限制（真实 API 缺 p95/速率时显示 `—`；不迁移已保存 skin）。不合并。
