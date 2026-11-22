# 管理面板布局契约

状态：自 2026-11-21 / PR #50 起生效。

适用范围：`nonebot/dashboard.html` 的结构、内联 CSS，以及 `appScript` 中与页面显示相关的部分。任何会改变管理面板页面、侧栏、卡片或移动端布局的改动都必须遵守本文件。

## 一句话模型

管理面板固定为：**概览 + 每个侧栏一级栏目一页 + Bot 基本设置二级页**。

- 概览只放 page-head、hero、CPU/内存/磁盘/Token KPI 与 footer。
- 每个一级栏目只显示一张全宽栏目卡片。
- Bot 基本设置继续用二级导航：基本信息 / 人格模板 / 模型设置 / 主人识别 / 超级管理员 / Token 消耗。

## 页面骨架

`main.workspace` 的直接子元素顺序必须是：

1. `header.page-head`
2. `#appMessage`
3. `#overview`
4. `#botBasicsNav`
5. `#panelGrid`
6. `footer.page-footer`

硬约束：

- `.page-head` 在所有页面可见，不得移进 `#overview` 或 `#panelGrid`。
- `h1#pageTitle` 是页面唯一主标题；`p#pageDesc` 显示概览问候、一级页所属侧栏分组，或 Bot 子页的「Bot 基本设置」（入口页显示侧栏分组「工作台」）。
- `#overview` 只包含 `.hero` 与 `.kpis`；不得放入 `section.card`。
- `#panelGrid` 必须使用块布局，直接子元素是每个栏目一个 `section.card`；`#appearancePopover` 是固定的浮层，不属于页面卡片。
- `#botBasicsNav` 在概览和一级栏目页隐藏；仅当当前页是 `#bot-info`、`#persona`、`#model-pref`、`#tokens`、`#master`、`#superusers` 时显示。
- `.page-footer` 保持在 `.workspace` 末尾。

## 侧栏顺序

| 分组 | 一级页面 |
| --- | --- |
| 工作台 | 概览、Bot 基本设置、表情包、用户记忆、群聊话题、服务器状态、插件管理 |
| 服务与订阅 | 天气助手、游戏活动、Epic 喜加一、B站动态、灾害预警、链接解析 |
| 知识与渲染 | 知识库 Embedding、图片渲染服务 |
| 运维 | 运行状态、解析日志 |
| 安全 | 管理员密码 |
| 外部服务 | 游戏账号控制台（`.external`，不参与分页） |

- `#overview` 是第一个一级链接，也是默认页。
- 无 hash、未知 hash 都回退概览。
- 每个一级链接（除概览和 `.external`）必须有对应的 `section.card`；每个 Bot 二级链接也必须有；每个非 auth card 必须出现在一级或二级导航中。不得有孤儿 card 或空导航。
- 页面切换只能显示当前 card；概览不得显示 card。

## 视觉约束

- **全宽卡片**：`#panelGrid` 保持 `display:block`；不得恢复 12 栏 grid、`grid-column`、span 类或其他概览网格布局。
- **单一标题**：栏目页的 page-head 使用 card 的 `data-page-title`/`h2` 作为标题；卡片自身 h2 在栏目页视觉隐藏（`body[data-page="section"] #panelGrid > .card:not([hidden]) .card-head h2{display:none}`），保留图标、说明和表单。不得恢复与 page-head 重复的卡片主标题。
- **无滚动 spy**：侧栏高亮由当前 hash 决定，不使用滚动位置推断当前栏目。
- **sticky page-head**：栏目页可以滚动长表单，但 page-head 保持现有 sticky 外观；卡片保留 `scroll-margin-top`。
- **移动端**：800px 断点沿用抽屉、遮罩、顶部主题按钮；概览 KPI 2 列；栏目卡片全宽；390px 不得出现横向溢出。
- **主题**：12 套主题都必须保持卡片全宽、无横向溢出；圣杯布局只保留左侧 sidebar 和主内容，右侧不显示 context rail / 页内导航；其他主题不得为了布局单独破坏分页结构。

## 首次启动引导

- 首次改密成功后写入 `data/dashboard-onboarding.json`；下次进入面板自动跳到当前步骤对应的 hash（第一步为 `#bot-info`）。
- `#basicsGuide` 是 `#app` 内的固定底部引导条，不是 `section.card`，也不属于 `#panelGrid`；不改变页面骨架和导航契约。
- 引导分五步：基本信息 / 人格模板 / 模型设置 / 主人识别 / 超级管理员。上一步、下一步切换并持久化 `basics_step`；最后一步完成或跳过时调用 `POST /api/onboarding`，之后不再显示。
- 手动点击 Bot 二级导航时，引导条同步到对应步骤；切换步骤会短暂防抖后保存进度。
- 引导未完成时，插件首次引导延后到 Bot 基本设置引导完成或跳过后再显示。

## 允许的改动

- 修改卡片内的表单、文案、API、校验和主题 token。
- 按本契约新增一个一级页面或 Bot 二级页面。
- 调整卡片内部间距、组件样式，只要不改变页面骨架、全宽卡片和标题归属。
- 新增侧栏分组（必须同时更新本文件的顺序表、契约测试和页面验证）。

## 不允许的改动

未先更新本契约、设计文档和测试的情况下，不允许：

- 把所有栏目重新堆叠到单页长滚动。
- 把栏目卡片放回 `#overview`，或让概览显示 card。
- 恢复 12 栏概览 grid、`grid-column`、span 类。
- 在圣杯布局恢复右侧 context rail / 页内导航，或给主内容让出右侧导航宽度。
- 恢复滚动 spy，或让侧栏高亮不再由 hash 决定。
- 移除/隐藏 `.page-head`，或把页面主标题移回卡片 h2。
- 在 `#panelGrid` 之外新增栏目 card。
- 新增导航项但没有对应 card，或新增 card 但没有导航入口。
- 改变一级导航顺序、分组边界或 Bot 二级导航，而不更新契约测试。
- 改动 hash 路由、默认页或深链接行为。

## 新增/调整页面的流程

1. **新增一级页面**：在正确分组插入 nav link；在 `#panelGrid` 加同 id 的 `section.card`，带 `h2` 作为页头标题和 card-head 说明；如需要加载数据，补 `sectionLoaders`。
2. **新增 Bot 二级页面**：在 `#botBasicsNav` 插入链接，并加同 id 的 card；页面归属自动走「Bot 基本设置」。
3. **新增分组**：加 `.nav-caption`；同步更新本文件的分组表、导航契约测试与验证记录。
4. **调整顺序**：同步更新 `test_bilibili_panel.py` 中的明确主顺序和本文件；不得只改测试或只改页面。
5. **布局变更**：必须先取得明确设计决定，再更新本文件、`2026-11-21-dashboard-category-pages-design.md`、契约测试和 PR 验证记录。

## 契约测试

以下测试是布局回归门禁，不得删除或弱化：

- `nonebot/tests/test_dashboard_layout.py`：概览/卡片分离、page-head 位置、侧栏到 card 的映射、全宽 CSS、card h2 隐藏、移动端 hook。
- `nonebot/tests/test_bilibili_panel.py::test_sidebar_keeps_primary_order_and_groups_bot_settings`：一级导航顺序、Bot 二级导航、导航与 section 的全集一致。

只有**有意的布局契约变更**才可更新断言；更新时必须在 PR 中说明原因，并同时更新本文件。

## 提交前验证

1. 相关 pytest（至少包含 dashboard、theme、appearance、bilibili、epic、game、persona、version，以及 `test_dashboard_layout.py`）。
2. 内联脚本 `node --check`；DOM 无重复 ID。
3. Chromium/Playwright：
   - 1440×1000 逐页检查全部一级和二级页面：概览无 card，栏目页只有一个可见 card，页头标题/分组正确。
   - 1024×768 抽查布局；390×844 检查概览、栏目页、移动抽屉、主题面板，无横向溢出。
   - 浏览器前进/后退、未知 hash 回退概览。
   - 12 套主题无横向溢出和 JS console error。
4. PR 写明：布局影响、实际运行的命令与结果、未验证项。涉及可见布局时附截图或说明截图未覆盖的原因。

## 已知取舍

- 长表单页面（如运行状态、插件管理、游戏活动）仍可能超过一屏；这是栏目内部内容长度，不允许为了缩短页面而把多个栏目合并。
- 卡片 h2 在栏目页视觉隐藏，屏幕阅读器以 page-head 的 h1 为页面标题；如果未来需要卡片级可访问名称，应在不恢复重复主标题的前提下给可见 card 加 `aria-label`。
