# 管理面板外观系统设计

日期：2026-11-19
状态：已按用户选择确定，进入实施
范围：`nonebot/dashboard.html`、`nonebot/dashboard.py`、新增 `nonebot/appearance_panel.py`

## 1. 背景

管理面板是单文件 `nonebot/dashboard.html`（1215 行、约 168 KB）：一个内嵌 `<style>`（约 16.8 KB / 229 条规则）和一个内嵌 `<script>`。现有 10 个 `:root` 变量、62 处 `var()`，但仍有 113 个硬编码颜色（CSS 102、HTML 内联 4、JS/SVG 6、rgba 1），且没有主题、深色模式或持久化。

`dashboard.py` 的 `GET /` 每次请求读取 HTML 并替换 `{{APP_VERSION}}`，无构建链、无 CDN、无 UI 框架。因此主题系统可以直接在单文件内完成，改 HTML/CSS 刷新即生效，改 Python 需重启面板。

## 2. 用户决策

| 决策 | 选择 |
|---|---|
| 持久化 | 浏览器 `localStorage`，按设备 |
| 主题 | 默认（当前）+ macOS 毛玻璃 / 新拟物 / 吉卜力 / 水彩 / 沉浸摄影（每个 StyleKit 链接一个）；移除独立配色设置 |
| 样式 | 圆角/阴影 + 字号/密度（舒适/紧凑/宽松） |
| 背景 | 内置渐变/纹理 + 图片 URL + 面板上传；主题可提供默认背景 |
| 背景范围 | 面板内切换「仅顶部 hero / 全屏」 |
| 默认 | 跟随系统 `prefers-color-scheme`，可手动覆盖 |
| 入口 | 侧栏底部按钮 + 移动端顶栏按钮 |
| 默认外观 | 当前浅色外观原样保留 |
| 明确不做 | 面板与机器人卡片统一主题；布局级换肤（侧栏位置/栅格重排） |

## 3. 架构

### 3.1 数据属性

所有外观状态挂在 `<html>` 上，CSS 只依赖属性与 token：

| 属性 | 值 |
|---|---|
| `data-theme` | `light` / `dark`（已解析；固定明暗主题会覆盖） |
| `data-theme-mode` | `system` / `light` / `dark`（用户选择） |
| `data-skin` | `default` / `macos` / `neumorphism` / `ghibli` / `watercolor` / `photo` / `korean` / `shader` |
| `data-style` | `comfortable` / `compact` / `large` |
| `data-bg` | `none` / `preset` / `url` / `upload` |
| `data-bg-preset` | `paper` / `dawn` / `forest` / `night` / `grid` |
| `data-bg-scope` | `hero` / `full` |

`localStorage` 键：`qqbot.dashboard.appearance.v1`。值为：

```json
{
  "mode": "system",
  "skin": "default",
  "style": "comfortable",
  "background": {
    "kind": "none",
    "preset": "paper",
    "value": "",
    "scope": "hero"
  }
}
```

任何字段非法或读取失败时回退默认值；URL 仅接受 `http:` / `https:`，上传背景仅接受本面板返回的 `/api/appearance/background/<name>`。

### 3.2 防闪烁

在原 `<style>` 之后新增 `<style id="appearanceTheme">`，随后放一个独立 `<script id="appearanceBoot">`，在 body 解析前读取 localStorage、设置 `data-*` 和 `--app-bg`。主脚本通过 `window.Appearance` 复用同一套 `read/apply/save`，避免两套逻辑。

### 3.3 Token 层

原 CSS 中的颜色按语义替换为 `var(--token)`，新增 token 的浅色值必须与替换前完全一致（默认外观零回归）。深色主题只覆盖 token，不改选择器。核心 token 语义：

- 表面：`--bg`、`--paper`、`--sidebar`、`--surface-2/3`、`--surface-hover/active/selected`
- 线条：`--line`、`--line-strong/soft/faint/mid/hover`
- 文字：`--ink`、`--ink-soft`、`--text-2..12`、`--muted`、`--text-topic*`、`--nav-icon`、`--dot`
- 强调/状态：`--accent`、`--accent-dark`、`--accent-soft`、`--on-accent`、`--danger*`、`--success`、`--warn*`、`--neutral-*`、`--focus`
- 图表/背景：`--chart-fill`、`--topic-fill`、`--track`、`--shadow-card`、`--shadow-dialog`、`--backdrop`、`--paper-translucent`、`--sidebar-translucent`
- 样式：`--fs-10..--fs-37`、`--r-8/9/10/11/12/20/999`、`--pad-card`、`--pad-workspace`、`--control-h`、`--nav-pad`、`--gap-card`、`--gap-grid`

浅色 token 值取当前 CSS 的原色；深色值采用暖炭黑表面 + 提亮文字/强调色。`--on-accent` 在浅色为 `#fff`，深色为 `#1b1310`，保证按钮文字对比度。

### 3.4 主题预设

默认主题 `default` 保持当前外观；七个 StyleKit 主题每个链接一个，覆盖颜色、表面、阴影、圆角、字体与默认背景。明暗模式保留：macOS/新拟物/吉卜力/水彩各有浅/深两套；沉浸摄影、着色器渐变固定暗色，韩式极简固定浅色。固定明暗的主题会禁用「明暗」单选并显示提示；切回自由主题时恢复用户原先的 system/light/dark 选择。

| 主题 | 来源 | 关键特征 |
|---|---|---|
| 默认 `default` | 当前面板 | 暖纸底、暖橙强调；零视觉回归 |
| macOS 毛玻璃 `macos` | https://www.stylekit.top/zh/styles/macos-vibrancy | 三层深灰、80% 侧栏 + backdrop-blur、system blue、Georgia 标题 |
| 新拟物 `neumorphism` | https://www.stylekit.top/zh/styles/neumorphism#style-prompts | `#e0e5ec` 同色基底、双阴影、无边框、blurple 强调 |
| 吉卜力 `ghibli` | https://www.stylekit.top/zh/styles/ghibli-style | 奶油/鼠尾草/天青、24px 卡片、胶囊按钮、天空渐变 |
| 水彩 `watercolor` | https://www.stylekit.top/zh/styles/watercolor-style | 纸白、蓝/蜜桃/水青晕染、衬线斜体标题、无硬边 |
| 沉浸摄影 `photo` | https://www.stylekit.top/zh/styles/immersive-photo | 全幅照片/电影感渐变、墨色 + 琥珀、玻璃卡片、全大写标签；自定义照片背景带 28s Ken Burns 漂移；固定暗色 |
| 韩式极简 `korean` | https://www.stylekit.top/zh/styles/korean-minimal | 暖白 + 石板蓝、藕粉/鼠尾草/米沙、24px 大圆角、16px 控件、无全大写/宽字距；固定浅色 |
| 着色器渐变 `shader` | https://www.stylekit.top/zh/styles/shader-gradient | 近黑画布上的实时 WebGL 虹彩场、24px 玻璃卡片、紫色 UI 强调、全大写小标签；固定深色 |

固定明暗与实现细节：

- `korean` 强制 `data-theme="light"`；`photo`/`shader` 强制 `data-theme="dark"`；`data-theme-mode` 保留用户原始选择，切回自由主题时恢复。
- `shader` 是一个独立的全屏 WebGL fragment shader 层：DPR 上限 2；页面隐藏或离屏时暂停 `requestAnimationFrame`；`prefers-reduced-motion` 时只渲染一帧；WebGL 不可用时回退为同配色的静态 CSS 渐变；文字与场之间有一层可读性遮罩。
- `photo` 的自定义 URL/upload 背景使用 28s alternate Ken Burns（`transform` only），`prefers-reduced-motion` 时静止。

主题只通过 `<html data-skin>` 覆盖 token 与少量组件规则；着色器主题额外有一个独立 canvas 渲染层；样式预设仍控制字号/圆角/间距。主题的背景默认仅在 `data-bg="none"` 时显示，用户选择 preset/URL/upload 后覆盖。

### 3.5 样式预设

- `comfortable`：当前值（15px 正文、16px 卡片圆角、26/28 卡片内边距、40px 控件高度）。
- `compact`：正文 14px、卡片圆角 12px、卡片内边距 20/22px、控件 34px、导航/栅格间距收紧。
- `large`：正文 16px、卡片圆角 20px、卡片内边距 32/34px、控件 46px、导航/栅格间距放宽。

只改尺寸 token，不改布局与断点；移动端 800px / 520px 媒体查询保持原结构。

### 3.6 背景

- 新增 `#bgLayer`（`position:fixed`，`z-index:-1`）承载背景；`data-bg="none"` 时默认隐藏，主题可提供默认背景（macOS/Ghibli/Watercolor/Photo），用户选择 preset/URL/upload 后覆盖。
- `hero`：层高约 620px，底部渐隐；卡片保持不透明。
- `full`：层铺满视口；卡片/侧栏/登录卡使用 `--paper-translucent` / `--sidebar-translucent` + `backdrop-filter: blur(12px)`。
- 预设：`paper`、`dawn`、`forest`、`night`、`grid`，各含浅色/深色两套渐变。
- URL：仅 `http(s)`，写入 `--app-bg: url("...")`；加载失败时保留纯色底。
- 上传：`POST /api/appearance/background`（原始字节 + `X-CSRF-Token` + 同源 Origin），`GET /api/appearance/background/{name}`（需登录）。
- 存储：`data/appearance-backgrounds/`，随机名，保留最近 20 张；5 MB 上限；PIL 校验 PNG/JPEG/WebP/GIF（≤4096、≤16MP），拒绝 SVG；响应带 `X-Content-Type-Options: nosniff`。

### 3.7 入口与交互

- 桌面：侧栏 `.side-bottom` 的「◐ 外观」按钮。
- 移动端（≤800px）：`.topline` 右侧的「◐」按钮。
- 弹出面板：明暗、主题、样式、背景四组；关闭按钮、Escape、点击外部关闭；`aria-expanded` 同步。
- 登录页不提供入口，但已保存的主题/背景会应用。

## 4. 非目标

- 不统一机器人出图卡片（`status_image.py` 等独立渲染管线）。
- 不做侧栏位置、栅格重排等布局级换肤。
- 不做服务端外观设置同步；面板只有一个管理员口令，按设备 localStorage 是本期契约。
- 不引入 CSS 框架、构建链或 CDN。

## 5. 兼容性与测试

- 更新 `nonebot/tests/test_game_event_panel.py`：从 `id="appScript"` 抽取主脚本，避免新增的 bootstrap `<script>` 被误取；为假 DOM 补 `localStorage` / `matchMedia` / `documentElement` 的缺失保护。
- 新增 `nonebot/tests/test_appearance_panel.py`：上传成功/格式非法/超限/未登录/无 CSRF/路径穿越。
- 现有 `test_bilibili_panel.py` 的导航断言不受影响：外观入口是 `<button>`，不新增导航链接或 section。
- 验收：定向 pytest + 全量 pytest；Playwright 截图覆盖浅色/深色、五个主题 × 明暗、三样式、预设/URL/上传背景、hero/full、移动端 800px；默认浅色与基线截图对比无可见差异。

## 6. 实施阶段

1. Token 化 + 浅/深色 + 跟随系统 + 侧栏/移动入口 + localStorage。
2. 五个 StyleKit 主题 + 三档样式（字号/圆角/间距）；移除独立配色设置。
3. 背景预设/URL/上传 + hero/full + 半透明表面。
4. 全量验证、分阶段提交、PR（目标 `main`，不自行合并）。
