# 管理面板外观系统设计

日期：2026-11-19
状态：已实施；主题设置 v2 已落地（2026-11-20）
范围：`nonebot/dashboard.html`、`nonebot/dashboard.py`、新增 `nonebot/appearance_panel.py`、新增 `nonebot/theme_panel.py`、新增 `nonebot/tests/test_theme_panel.py`

## 1. 背景

管理面板是单文件 `nonebot/dashboard.html`（当前 2176 行、约 284 KB）：两个内嵌 `<style>`（基础样式 + `appearanceTheme`）和两个内嵌 `<script>`（`appearanceBoot` + `appScript`）。外观/主题/背景/预设状态全部由 token、`data-*` 属性和 `localStorage` 驱动；`dashboard.py` 的 `GET /` 每次请求读取 HTML 并替换 `{{APP_VERSION}}`，无构建链、无 CDN、无 UI 框架。

因此主题系统直接在单文件内完成：改 HTML/CSS 刷新即生效，改 Python 需重启面板。

## 2. 用户决策

| 决策 | 选择 |
|---|---|
| 持久化 | 浏览器 `localStorage`，按设备 |
| 主题 | 默认（当前）+ 十一个 StyleKit 主题（macOS 毛玻璃 / 新拟物 / 吉卜力 / 水彩 / 沉浸摄影 / 韩式极简 / 着色器渐变 / 圣杯布局 / 北欧极简 / 液态玻璃 / 暗黑学院）；移除独立配色设置 |
| 样式 | 圆角/阴影 + 字号/密度（舒适/紧凑/宽松） |
| 背景 | 内置渐变/纹理 + 图片 URL + 面板上传；主题可提供默认背景 |
| 背景范围 | 面板内切换「仅顶部 hero / 全屏」 |
| 默认 | 跟随系统 `prefers-color-scheme`，可手动覆盖 |
| 入口 | 侧栏底部按钮 + 移动端顶栏按钮 |
| 默认外观 | 当前浅色外观原样保留 |
| 明确不做 | 面板与机器人卡片统一主题；除圣杯布局主题外，不做布局级换肤（侧栏位置/栅格重排） |

## 3. 架构

### 3.1 数据属性

所有外观状态挂在 `<html>` 上，CSS 只依赖属性与 token：

| 属性 | 值 |
|---|---|
| `data-theme` | `light` / `dark`（已解析；固定明暗主题会覆盖） |
| `data-theme-mode` | `system` / `light` / `dark`（用户选择） |
| `data-skin` | `default` / `macos` / `neumorphism` / `ghibli` / `watercolor` / `photo` / `korean` / `shader` / `holy` / `scandi` / `liquid` / `academia` |
| `data-style` | `comfortable` / `compact` / `large` |
| `data-bg` | `none` / `preset` / `url` / `upload` |
| `data-bg-preset` | `paper` / `dawn` / `forest` / `night` / `grid` |
| `data-bg-scope` | `hero` / `full` |
| `data-sidebar-layout` | `docked` / `floating` / `line` |
| `data-icon-color` | `colored` / `white` / `black` |

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
  },
  "misc": {
    "radius": null,
    "sidebarLayout": "docked",
    "iconColor": "colored"
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

默认主题 `default` 保持当前外观；十一个 StyleKit 主题每个链接一个，覆盖颜色、表面、阴影、圆角、字体与默认背景。明暗模式保留：macOS/新拟物/吉卜力/水彩/液态玻璃各有浅/深两套；沉浸摄影、着色器渐变、暗黑学院固定暗色；韩式极简、圣杯布局、北欧极简固定浅色。固定明暗的主题会禁用「明暗」单选并显示提示；切回自由主题时恢复用户原先的 system/light/dark 选择。

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
| 圣杯布局 `holy` | https://www.stylekit.top/zh/styles/holy-grail-layout | 240px 左栏 / 弹性主栏、56px sticky header、48px footer、24px 间距、12px 卡片；固定浅色；唯一布局级主题 |
| 北欧极简 `scandi` | https://www.stylekit.top/zh/styles/scandinavian | 暖灰白 `#f5f0eb`、炭灰/木色/苔绿、2px 圆角、无阴影、700ms 过渡、极细字重；固定浅色 |
| 液态玻璃 `liquid` | https://www.stylekit.top/zh/styles/liquid-glass | 暖纸实色正文卡 + 玻璃侧栏/顶栏/弹层、SVG 位移边缘折射、按压/回弹；浅深两套 |
| 暗黑学院 `academia` | https://www.stylekit.top/zh/styles/dark-academia | 深棕/墨绿/暗金、奶白纸面卡、衬线字体、2px 圆角、1s 慢过渡；固定暗色 |

固定明暗与实现细节：

- `holy`/`scandi` 强制 `data-theme="light"`；`photo`/`shader`/`academia` 强制 `data-theme="dark"`；`liquid` 保留明暗切换；`data-theme-mode` 保留用户原始选择，切回自由主题时恢复。
- `holy` 是唯一布局级主题：`.workspace` 为 240px 左栏 + 弹性主栏（右侧不再有导航栏），`.topline` sticky 56px，`.holy-footer` 48px；≤800px 沿用移动端导航。
- `liquid` 将正文卡保持为 `#fcfcfa` 实色；侧栏、浮动顶栏、外观弹层与确认框使用半透明染色 + 背景模糊。边缘折射通过内联 SVG `feTurbulence + feDisplacementMap` 和 mask 只作用于 12px 边框带；不支持时保留磨砂玻璃。按压约 110ms、释放约 420ms 回弹；`prefers-reduced-motion` / `prefers-reduced-transparency` 有降级。
- `shader` 是一个独立的全屏 WebGL fragment shader 层：DPR 上限 2；页面隐藏或离屏时暂停 `requestAnimationFrame`；`prefers-reduced-motion` 时只渲染一帧；WebGL 不可用时回退为同配色的静态 CSS 渐变；文字与场之间有一层可读性遮罩。
- `photo` 的自定义 URL/upload 背景使用 28s alternate Ken Burns（`transform` only），`prefers-reduced-motion` 时静止。
- `scandi` 使用 2px 圆角、无阴影、仅底边的输入线和 700ms ease-in-out；外观弹层使用 `#fbf9f5` 实色，主体内容卡仍保留 60% 白；`academia` 的卡片在局部重定义文字/边框 token 为深棕，页面与侧栏保持深棕/墨绿。

主题主要覆盖 token 与组件规则；圣杯布局额外调整 `workspace/topline/footer`，着色器额外有 canvas 层，液态玻璃额外有 SVG 边缘滤镜；样式预设仍控制字号/间距/控件高度。主题的背景默认仅在 `data-bg="none"` 时显示，用户选择 preset/URL/upload 后覆盖。

### 3.5 样式预设

- `comfortable`：当前值（15px 正文、16px 卡片圆角、26/28 卡片内边距、40px 控件高度）。
- `compact`：正文 14px、卡片圆角 12px、卡片内边距 20/22px、控件 34px、导航/栅格间距收紧。
- `large`：正文 16px、卡片圆角 20px、卡片内边距 32/34px、控件 46px、导航/栅格间距放宽。

只改尺寸 token，不改布局与断点；移动端 800px / 520px 媒体查询保持原结构；圣杯布局主题是唯一布局级例外。

### 3.6 背景

- 新增 `#bgLayer`（`position:fixed`，`z-index:-1`）承载背景；`data-bg="none"` 时默认隐藏，主题可提供默认背景（macOS/Ghibli/Watercolor/Photo/Korean/Shader/Liquid/Academia），用户选择 preset/URL/upload 后覆盖。
- `hero`：层高约 620px，底部渐隐；卡片保持不透明。
- `full`：层铺满视口；卡片/侧栏/登录卡使用 `--paper-translucent` / `--sidebar-translucent` + `backdrop-filter: blur(12px)`。
- 预设：`paper`、`dawn`、`forest`、`night`、`grid`，各含浅色/深色两套渐变。
- URL：仅 `http(s)`，写入 `--app-bg: url("...")`；加载失败时保留纯色底。
- 上传：`POST /api/appearance/background`（原始字节 + `X-CSRF-Token` + 同源 Origin），`GET /api/appearance/background/{name}`（需登录）。
- 存储：`data/appearance-backgrounds/`，随机名，保留最近 20 张；5 MB 上限；PIL 校验 PNG/JPEG/WebP/GIF（≤4096、≤16MP），拒绝 SVG；响应带 `X-Content-Type-Options: nosniff`。

### 3.7 入口与交互

- 桌面：侧栏 `.side-bottom` 的「◐ 主题设置」按钮。
- 圣杯布局：沿用侧栏「主题设置」入口；桌面仅保留左侧导航 + 主内容，右侧不再有导航栏。
- 移动端（≤800px）：`.topline` 右侧的「◐」按钮。
- 弹出面板：四个 tab（主题预设 / 外观 / 背景 / 杂项配置），默认「外观」；关闭按钮、Escape、点击外部关闭；`aria-expanded` 同步。
- 登录页不提供入口，但已保存的主题/背景会应用。

### 3.8 主题设置 v2

在原有「外观」基础上增加命名预设与杂项配置，入口仍是侧栏/移动端弹层，不新增 dashboard section 或导航链接。

- **四个 tab**：`主题预设`（保存/列表/应用/删除）、`外观`（明暗/12 skin/密度）、`背景`（预设/URL/上传/仅顶部/全屏/清除）、`杂项配置`（圆角/侧边栏布局/图标颜色）。
- **活动外观仍是按设备 localStorage**：key 仍为 `qqbot.dashboard.appearance.v1`，新增向后兼容的 `misc: {radius, sidebarLayout, iconColor}`；旧值缺 `misc` 时补 `null / docked / colored`，视觉不变。活动外观不写服务端。
- **命名预设是服务端快照**：存 `nonebot/data/theme-presets.json`，单文件 + 锁 + 临时文件 + `os.replace` 原子写；4 个内置预设，用户预设最多 50 个；名称、枚举、范围、URL/上传路径和文件大小均校验。
- **4 个 API**：`GET /api/theme/presets`（`authorized`）；`POST /api/theme/presets`、`POST /api/theme/presets/apply`、`POST /api/theme/presets/delete`（均 `mutate`，CSRF + Origin）。`apply` 只返回配置并由客户端写入 localStorage，**不写服务端活动状态**。
- **故意不支持** gsuid_core 的 `language`、`ui_scale`、`shadow_intensity`、卡片透明度/背景模糊、侧栏收起：当前 CSS/布局没有无损对应机制，避免伪功能。
- **边界**：`holy` 和 ≤800px 时侧边栏布局控件禁用并提示；圆角覆盖 `--r-8..--r-20`/`--radius`，保留 `--r-999`；预设名以 `textContent` 渲染。

## 4. 非目标

- 不统一机器人出图卡片（`status_image.py` 等独立渲染管线）。
- 除圣杯布局主题外，不做侧栏位置、栅格重排等布局级换肤。
- 不做**活动外观**的服务端同步；当前生效外观仍按设备 localStorage。命名预设是服务端快照（见 3.8），不是活动配置同步。
- 不引入 CSS 框架、构建链或 CDN。

## 5. 兼容性与测试

- 更新 `nonebot/tests/test_game_event_panel.py`：从 `id="appScript"` 抽取主脚本，避免新增的 bootstrap `<script>` 被误取；为假 DOM 补 `localStorage` / `matchMedia` / `documentElement` 的缺失保护。
- 新增 `nonebot/tests/test_appearance_panel.py`：上传成功/格式非法/超限/未登录/无 CSRF/路径穿越。
- 新增 `nonebot/tests/test_theme_panel.py`：4 个预设 API 的鉴权/CSRF、保存/列表/应用/删除、覆盖、内置保护、非法名称/配置（含畸形 URL）、损坏文件、原子写、数量与体积上限。
- 现有 `test_bilibili_panel.py` 的导航断言不受影响：入口是 `<button>`，不新增导航链接或 section。
- 验收：定向 pytest（`test_theme_panel`、`test_appearance_panel`、`test_game_event_panel`、`test_bilibili_panel` 导航断言）；浏览器截图以**手工/隔离 harness** 证据为准（非自动化 Playwright 测试），覆盖浅色/深色、十二个主题（固定明暗主题按固定方案）、三样式、预设/URL/上传背景、hero/full、四 tab、预设 CRUD/当前徽标、杂项圆角/侧栏布局/图标色、`holy` 与移动端约束、圣杯布局 header/footer 与液态玻璃边缘滤镜；默认浅色与基线截图对比无可见差异。
- 已知环境问题：`test_dashboard.py` 有 2 个既有 `nonebot/data/weather.sqlite3` 权限失败，与外观/主题改动无关；全量 pytest 因此不可声称通过，报告需如实注明。

## 6. 实施阶段

1. Token 化 + 浅/深色 + 跟随系统 + 侧栏/移动入口 + localStorage。
2. 十一个 StyleKit 主题 + 三档样式（字号/圆角/间距）；移除独立配色设置。
3. 背景预设/URL/上传 + hero/full + 半透明表面。
4. 主题设置 v2：四 tab、服务端命名预设、杂项圆角/侧栏布局/图标色（见 3.8）。
5. 全量验证、分阶段提交、PR（目标 `main`，不自行合并）。
