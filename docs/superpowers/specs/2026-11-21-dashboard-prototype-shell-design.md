# 管理面板：PR #46 原型外壳落地设计

日期：2026-11-21
状态：待批准
规模：中等（单文件外壳 + 默认皮肤 + 响应式）

## 背景

PR #46 新增 `docs/research/prototypes/dashboard-preview.html`：静态 mock，默认 `korean`，五套可切换 skin，使用 page-head / hero / card tray / 12 栏 grid / 圣杯 context rail 的外壳。

正式面板 `nonebot/dashboard.html` 已有 11 套 skin（`default`、`korean`、`holy` 等）和 theme token；当前默认仍是 `default`，外壳是 sidebar / workspace / topline / intro / card 的单列结构。用户选择：

- **B：移植原型外壳**，保留现有真实 panel sections、JS 和 API。
- 已保存的 `localStorage` skin 不强制迁移；只改新会话默认。

## 目标

- 新会话默认 `korean`；已有 `qqbot.dashboard.appearance.v1` 的显式 skin 原样保留。
- 外壳：浮动 sidebar（bot card、分组 caption、side-bottom）、page-head（标题/描述/链路/刷新/主题）、hero（bot 状态/结论/操作/侧栏事实）、KPI 行、card tray、12 栏概览 grid、圣杯 context rail、页脚。
- 真实数据来自现有 API；不可用指标显示 `—`，不伪造历史。
- 1440 / 1024 / 390 三档响应式；无横向溢出；键盘 focus；reduced motion。
- 保留现有 section ID、事件绑定、登录/CSRF、API 与可访问性。

## 非目标

- 不按原型重构成 28 面板 router，不新增信息架构或后端 API。
- 不强制迁移已有浏览器 skin，不改服务器 `default` 预设。
- 不改 auth、SnowLuma、RAG、机器人进程或构建链。
- 12 栏只用于概览/摘要卡；表单、表格、长编辑器在窄屏或需要时保持全宽/单列。
- 不改 `docs/research/prototypes/dashboard-preview.html`。

## 方案

| 方案 | 结论 |
|---|---|
| A. 只把默认 skin 换成 Korean | 不选：只换颜色，不满足“主要风格布置”。 |
| B. 移植原型外壳，保留真实面板/JS/API | **选用**：视觉和布局到位，风险集中在 `dashboard.html`。 |
| C. 完整重构为 28 面板 router + 新概览数据 | 不选：需要新 API/IA，超出本次范围。 |

## 设计

### 外壳结构

```text
#app
├─ aside.sidebar        浮动卡片、brand、bot-card、nav-caption 分组、side-bottom
├─ main.page            page-head + hero + kpis + appMessage + basics-nav + panel-grid + footer
└─ aside.context-rail   仅 holy ≥1280px；当前 panel 标题 + 同组链接
```

- 现有 17 个 `<section id="...">` 保持 ID 不变，统一包进 `panel-grid`。
- page-head 的标题/描述默认“控制台”；滚动时用 scroll spy 取当前 section 的 `h2`/`section-description` 更新。
- hero 使用 `/api/bot-status` 的 ready/connected、bot 名称、版本和最近刷新时间；没有 24h 历史时不画伪造曲线，改为“当前链路”状态条（24 个 tick 表示本次连接状态）或隐藏图表，仅保留事实行。
- KPI 行：CPU/内存/磁盘来自 `/api/status/overview`，今日 Token 来自 `/api/token?days=7`；p95/消息速率没有现成数据时显示 `—`。
- sidebar 的 bot 名称复用 `applyBotName`；live dot 复用 bot status；`side-bottom` 保留主题/退出。
- 主题切换、登录、CSRF、`appMessage`、`basics-nav` 的 ID 与行为不变。

### 卡片与 12 栏

- `.card` 使用 4px tray 边（`--surface-2`/`--tray` token）+ 现有 paper 内容；保留各 skin 的颜色覆盖。
- 概览/摘要卡使用以下 span，长表单/表格默认 `span 12`；最终以 1024/1440 截图调整：

| Section | span |
|---|---|
| `bot-info` | 7 |
| `persona` | 12（长编辑器） |
| `memes` | 7 |
| `memory` | 5 |
| `group` / `weather` | 6 / 6 |
| `game-events` / `epic` | 6 / 6 |
| `bilibili` / `disaster` | 6 / 6 |
| `parser` | 12（字段密集） |
| `embedding` / `renderer` | 6 / 6 |
| `status` / `monitor` / `plugins` / `tokens` / `events` / `account` | 12 |

- `≤1100px` 全部单列；`1101–1279px` 只保留 7/5 与 6/6；`≥1280px` 完整 12 栏（holy 再让出 280px context rail）。

### 圣杯 context rail

- 仅 `html[data-skin="holy"]` 且 `≥1280px` 显示；宽 280px，右侧 fixed。
- 内容：当前 section 标题/描述、同组 section 链接、当前时间/版本；scroll spy 更新。
- `holy` 的 sidebar 保持 240px；workspace 右移 304px；`≤1279px` 隐藏 rail。

### 移动端

- `≤800px` sidebar 改为抽屉：page-head 左侧菜单按钮，打开加 `body.nav-open`/遮罩；点击 nav 链接后关闭；Esc 关闭。
- page-head/hero/KPI 堆叠；card 全宽；无横向溢出。
- `prefers-reduced-motion` 下关闭抽屉/卡片过渡。

### 默认 skin

- `<html data-skin="korean">` 作为无 JS 回退。
- `appearanceBoot` 的 `defaults()` 改为 `skin:'korean'`；只有 localStorage 无有效配置时生效。
- 服务器 `theme_panel.default_config()`/`default` 预设保持 `default`，避免改预设语义和现有测试。
- 已有 `skin=default` 的浏览器继续显示 default；用户主动切 Korean 即可。

### 样式与 JS 组织

- 新增 `<style id="prototypeShell">`，放在 `appearanceTheme` 之后；只做布局/尺寸，颜色走现有 token；补充 `--tray`、`--font-display` 等必要 token。
- 新增少量 ID/函数：`pageTitle`、`pageDesc`、`shellLive`、`heroBotName`、`heroVerdict`、`heroSideFacts`、`kpi*`、`navToggle`、`contextRail`、scroll spy、drawer。
- 在 `applyBotName`、`loadBotStatus`、`loadStatus`、`loadTokens`、`enterApp` 中更新新节点；不新增 fetch 端点。
- 不改变现有 section 的 `onclick`/`id`/表单提交。

## 文件

- `nonebot/dashboard.html`：外壳 CSS/HTML/JS、默认 boot skin。
- `nonebot/tests/test_dashboard.py`：新增关键标记/默认 skin/不破坏现有 API 的断言（如现有测试结构允许）。
- `CHANGELOG.md`：`[未发布]` 新增「管理面板」条目。
- 本设计文档。
- 不改后端、`.env`、数据或原型文件。

## 验证

1. `python -m pytest nonebot/tests/test_dashboard.py nonebot/tests/test_theme_panel.py nonebot/tests/test_appearance_panel.py -q`。
2. 本地 `dashboard.py --init` 创建临时密码，启动 uvicorn；Playwright 1440×1000 / 1024×768 / 390×844 截图。
3. 检查：新会话默认 Korean；写入 `skin=default` 的 localStorage 后仍是 default；切换 5 套原型 skin + holy 正常；page-head/hero/KPI/sidebar/context rail 可见；无 console error、无横向溢出。
4. 登录、主题切换、section 跳转、已有表单/按钮抽检；SnowLuma/机器人未运行时 API 错误不破坏外壳。
5. 生产部署留给 PR 合并后的更新流程。

## 风险与回滚

- 单文件 CSS 特异性冲突：新样式块放最后，使用 `#app`/`html[data-skin]` 作用域；逐 skin 截图。
- 12 栏挤压表单：先按上表，截图后把长表单改回 span 12；不牺牲可用性。
- 移动抽屉影响现有滚动导航：保留原有 `href="#..."`，只在点击后关闭抽屉。
- 回滚：revert 分支即可；不改数据/配置/localStorage schema。
