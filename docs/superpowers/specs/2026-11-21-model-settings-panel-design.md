# 管理面板「模型设置」：首选 / 次选模型

日期：2026-11-21
状态：已批准（用户确认「可以就这呀」）
规模：小改动

## 背景与目标

后端已有完整的模型首选/次选能力：

- `nonebot/model_pref_logic.py` 负责状态、条目名校验、降级判断；
- `nonebot/plugins/model_pref.py` 每回合按状态切换模型，并在首选失败时用次选重跑；
- 状态保存在 `nonebot/data/model_pref.json`。

生产状态是 `primary = relay`（真模型覆盖 `deepseek-v4.1-flash-eco`），`secondary` 未设置。此前只能在主人私聊里用 `.模型 首选/次选` 设置，管理面板没有入口。

目标：在管理面板「Bot 基本情况」下增加「模型设置」页，让主人用下拉框选择首选和次选条目；保存后下一条回复生效，不需要重启机器人。面板只选 `configs/models.yml` 条目；任意上游真模型名仍由 `.模型 首选/次选` 指令负责，因为面板没有可靠的网关清单可校验。

## 设计

### 页面

- 在 `nonebot/dashboard.html` 的 `#botBasicsNav` 中，「人格模板」之后增加 `模型设置`（link + section 同 id：`model-pref`）。
- 卡片沿用一页一张全宽卡片的布局；两个下拉：
  - 首选：`默认（models.yml 的 default 条目）` + 各条目；
  - 次选：`无（不降级）` + 各条目。
- 条目标签显示 `条目名 → model_name`；默认条目加「默认」标记。
- 只读展示当前选择、当前生效条目、真模型覆盖（`relay → deepseek-v4.1-flash-eco`）和今天的顶班次数。
- 配置未发布时显示警告并锁定保存，与「人格模板」一致。

### API

新增 `GET/POST /api/model-pref`，需要登录；POST 需要 CSRF 和同源校验。

`GET` 返回：

```json
{
  "models": [{"name": "relay", "model_name": "deepseek-v4.1-flash-eco", "default": true, "provider": "openai", "stream": true}],
  "default_model": "relay",
  "active_model": "relay",
  "active_source": "model_pref",
  "state": {"primary": "relay", "primary_model": "deepseek-v4.1-flash-eco", "secondary": null, "secondary_model": null, "swaps": {}, "updated": "..."},
  "publication": {"published": true, "reason": ""}
}
```

不返回 API Key、中转地址或模板正文。

`POST` 请求体固定为 `{"primary": "<条目名|null>", "secondary": "<条目名|null>"}`。

### 保存语义

1. 先确认 `configs/models.yml` 已发布；未发布返回 409，不写状态。
2. 条目名不区分大小写校验，写回 `configs/models.yml` 里的规范名称；空字符串或 `null` 表示清除。
3. 原状态的首选条目和请求条目相同（不区分大小写）时保留 `primary_model` 真模型覆盖；切换条目时清空。`secondary_model` 同理。
4. 保留 `swaps`；更新 `updated`；用 `save_state` 原子写盘。
5. 不修改 `models.yml`，不调用 MuiceBot。

### 机器人读取新状态

`plugins/model_pref.state()` 目前只在跨天时重读盘。面板是另一个进程，直接写 JSON 后机器人缓存不会变。给它加文件 token（mtime、ctime、size）：每次调用 `state()` 比对 token，变了就重读。`.模型` 指令写盘后同步更新 token。这样面板保存后，机器人下一条回复就按新首选/次选执行，不需要重启。

### 非目标

- 不在面板里输入任意上游真模型名（没有可靠清单，写错会变成「已读不回」）。
- 不修改 `.模型` 指令的语义和帮助。
- 不改 `models.yml`、API Key、中转地址或人设。
- 不新增主题或布局模式。

## 验证

- 新 API 的登录、CSRF、条目校验、真模型覆盖保留/清除、未发布 409、无密钥泄露。
- `plugins/model_pref.state()` 在外部写入状态文件后重读；`.模型` 指令仍然正常。
- 页面新二级导航、路由、加载器、布局契约测试；`node --check`。
- Chromium 打开「模型设置」页，确认两个下拉、当前状态、保存提示和未发布锁定。
