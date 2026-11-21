# 管理面板栏目分页设计

日期：2026-11-21

## 目标

- 主内容不再把所有栏目堆在一个长页面。
- 增加「概览」作为默认首页：保留 page-head、hero、KPI 与 footer。
- 每个侧栏一级栏目一页；页面只显示该栏目对应的 section 卡片。
- 「Bot 基本情况」保留二级导航：基本信息 / 人格模板 / Token 消耗 / 主人识别 / 超级管理员各自是一页。
- 保持现有 section ID、表单 ID、onclick、API、主题系统和深链接可用。

## 非目标

- 不改后端接口、数据库、配置或被 Git 忽略的数据。
- 不删除任何表单能力；只调整显示与页面切换。
- 不改主题色板；卡片仍使用现有 `.card` 主题样式和 4px tray outline。

## 方案

- 采用「概览 + 每个侧栏一级栏目一页」；侧栏分组 caption 只做导航分组，不单独成为页面。这样既满足分类浏览，又保留 Bot 基本情况的二级子页。
- 备选「每个侧栏分组一页」会让工作台仍包含 6 个以上栏目，页面仍然很长；备选「全部一页」是当前问题本身，均不采用。

## 实现约定

- `#overview` 包裹 hero + KPI；默认无 hash 时显示概览。
- 新增侧栏「概览」链接，href 为 `#overview`；其余链接保持原 hash。
- `#panelGrid` 改为块布局；只保留当前 section 可见，其余 `hidden`。
- 栏目页 page-head 标题 = 当前 section 的 `data-rail-title`/`h2`；副标题 = 所属侧栏分组（Bot 基本情况子页为「Bot 基本情况」）。
- 栏目页隐藏卡片自身的 `h2`，保留图标、说明、表单，避免与 page-head 重复主标题；说明仍在卡片头部完整显示。
- 概览页隐藏 basics-nav 和所有 section；栏目页隐藏 hero/KPI；basics-nav 仅在五个 Bot 基本情况子页显示。
- 页面切换使用现有 `hashchange` 和重复点击刷新逻辑；切换后回到页首，不新增后端请求。
- 超长表单页面允许正常滚动；sticky page-head 和 footer 保持现有外观。
- 移动端沿用抽屉；概览 KPI 2 列，栏目卡片全宽。

## 验证

1. 相关 pytest：dashboard、theme、appearance、bilibili、epic、game、persona、version 全绿。
2. 内联脚本 `node --check`，DOM 无重复 ID。
3. Playwright/Chromium：1440×1000、1024×768、390×844；概览、栏目切换、深链接、Bot 二级页、移动抽屉、12 套主题无横向溢出和 JS console error。
4. 确认概览页高度不再随全部栏目增长；栏目页只显示对应 section。
