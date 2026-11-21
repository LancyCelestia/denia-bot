# 模型设置：提供商 base URL / Key + 上游模型列表

日期：2026-11-21
状态：设计待批准
规模：中等改动

## 背景与目标

当前生产 `configs/models.yml` 有两个条目：

- `relay`：真正的提供商，`provider: openai`，`api_host: http://10.0.0.240:2333/v1`，Key 已配置，条目里的 `model_name` 是 `gemini-3.8-flash-high`，`default: true`，人设 `current`。
- `echo-test`：`provider: _echo`，本地回声测试，`model_name: echo`。

模型偏好状态是 `primary=relay`、`primary_model=deepseek-v4.1-flash-eco`、`secondary` 未设置。面板当前把这两个条目名直接当首选/次选下拉，所以用户看到的是 `relay` 和 `echo-test`，而不是真正的提供商和模型。

目标：在「模型设置」里直接维护**一个 OpenAI 兼容提供商**的 base URL 和 API Key，从该提供商的 `/v1/models` 获取上游模型列表，并从中选择首选/次选模型；界面不再显示 `relay`、`echo-test` 这类内部条目名。保留首选失败时自动切到次选的能力。`echo-test` 从面板隐藏但保留在 `models.yml` 中用于诊断。

用户已确认：共用一个提供商、隐藏 echo-test、保存 base URL/Key 后允许面板一键重启机器人。

## 目标

- 面板显示提供商 base URL、Key 是否已配置、当前模型，不显示 Key 明文。
- 面板可拉取上游模型列表并从中选择首选和次选。
- 首选/次选仍写 `data/model_pref.json`，下一条回复生效；保留自动降级。
- base URL/Key 仍写 `configs/models.yml`，保留注释、其他条目和其他字段；通过 `model_publication` 发布。
- 保存提供商后提示需要重载/重启，并提供一键重启机器人；模型选择本身不需要重启。
- 面板只展示默认提供商；`relay` 条目名和 `echo-test` 不出现在界面上。

## 非目标

- 不管理多个提供商，不做首选/次选各自独立的 base URL/Key。
- 不删除 `echo-test` 条目；需要时由维护者手动删除。
- 不改 `.模型` 指令的语义；条目名和上游真模型名仍可用。
- 不支持非 OpenAI 兼容的 `/models` 协议；`provider` 仍固定为 `openai`。
- 不自动重启机器人；由用户点击确认。
- 不把 Key 回传给浏览器，不写日志，不放进 URL。
- 不做模型列表的持久缓存；每次按需拉取，或页面打开时自动拉一次。

## 方案

| 方案 | 结论 |
|---|---|
| A. 共用一个提供商，首选/次选选两个模型 | **选用**。保留现有降级逻辑，UI 最接近用户描述，配置面最小。 |
| B. 首选/次选各自提供商 | 不选。需要两套 base URL/Key、两个模型列表和跨提供商降级；用户明确要“一个提供商”。 |
| C. 一个提供商一个模型，移除首选/次选 | 不选。会丢掉现有自动降级；用户确认保留首选/次选。 |

### 提供商条目

- 使用 `models.yml` 的默认条目（当前 `relay`）作为面板提供商。条目名只在后端使用，UI 不展示。
- 面板读取 `provider`、`api_host`、`api_key_configured`、`model_name`、`template`；不返回 `api_key`。
- `echo-test` 及其他非默认条目不出现在面板；`.模型` 指令仍能看到它们。

### 模型选择

- 首选/次选下拉的数据来自上游 `/v1/models`，不是 `models.yml` 条目名。
- 保存时写 `data/model_pref.json`：
  - `primary = <默认条目>`，`primary_model = <首选模型名>`；
  - `secondary = <默认条目>`，`secondary_model = <次选模型名>`；次选选“无”时两者都清空；
  - 保留 `swaps`，刷新 `updated`。
- `models.yml` 里条目的 `model_name` 保留为兜底默认值；模型选择通过 `primary_model` / `secondary_model` 覆盖，不要求改 `model_name`。
- 首选必填；次选可选。若两者模型名相同，面板允许保存但提示“同名不会降级”，与 `.模型` 指令的判定一致。
- 模型保存不重启，下一条回复生效。

### 提供商保存

- 新 API 只接受 `base_url` 和可选 `api_key`；`provider` 固定 `openai`，其他字段（人设、stream、multimodal、注释、其他条目）逐字节保留。
- Key 留空表示保持现有 Key；只有填写时才替换。响应只返回 `api_key_configured: true`。
- `base_url` 只接受 `http://` 或 `https://`，不允许 URL 内嵌账号密码；尾部 `/` 归一化；`/v1` 由用户自己填写（当前是 `http://10.0.0.240:2333/v1`）。
- 写 `models.yml` 使用行级重写 + `model_publication.publish_file`，失败时保留原文件；未发布状态返回 409。
- 保存成功返回 `restart_required: true`；面板显示“提供商配置已保存，需要重启机器人”，并提供一键重启按钮，复用现有 `/api/bot-restart`。模型选择保存返回 `restart_required: false`。

### 上游模型列表

- 新 API 接收 `base_url` 和可选 `api_key`；Key 留空时使用已保存的 Key。
- 面板服务器用 `httpx` 请求 `{base_url}/models`，带 `Authorization: Bearer <key>`，超时 10 秒，不跟随重定向，响应体上限 1 MiB，模型列表上限 500 个。
- 只读取 OpenAI 兼容响应里的 `data[].id`，去重、排序；返回模型 ID 列表，不返回 Key。
- 页面打开时如果已配置 Key，自动拉一次；用户也可以修改 base URL/Key 后手动点“获取模型列表”。
- 拉取失败保留当前选择并显示错误（连接失败、HTTP 状态、响应格式），不写任何配置。

### API

- `GET /api/model-pref`：返回 `provider`（`base_url`、`api_key_configured`、`model_name`、`template`）、`state`、`active_model`、`active_source`、`publication`；不含 Key、`echo-test` 和其他条目。
- `POST /api/model-pref/provider`：`{base_url, api_key?}`；校验、重写、发布；返回 provider 和 `restart_required: true`。
- `POST /api/model-pref/upstream`：`{base_url?, api_key?}`；返回 `{models: [id], source: base_url}` 或错误；不写配置。
- `POST /api/model-pref`：`{primary, secondary}`；模型名非空、长度上限 200；写偏好状态；返回 `restart_required: false`。
- 所有接口沿用登录、CSRF、同源校验；错误信息不回显 Key。

### 页面

- 仍放在「Bot 基本情况 → 模型设置」。
- 提供商区：base URL 输入框、API Key 密码输入框（占位符“已配置，留空保持不变”）、获取模型列表按钮、保存提供商按钮、重启提示和重启按钮。
- 模型区：首选下拉、次选下拉（含“无”）、保存模型设置按钮；显示当前生效模型、真模型覆盖和当天顶班次数。
- 不再显示 `relay`/`echo-test` 条目名；页面说明“这是默认提供商的 base URL 和模型；其他条目仍可用 `.模型` 指令管理”。
- 未发布 `models.yml` 时锁定保存提供商；模型选择仍可保存（状态文件不依赖发布）。
- Key 输入框永远不回填；GET 只显示“已配置”。

## 安全与约束

- Key 只写不读：响应、错误、日志、HTML 都不出现明文；只通过 `Authorization` 头发给用户填写的 base URL。
- base URL 由管理员填写，面板本身是内网管理面；仍限制协议、禁止 userinfo、限制响应大小和超时，降低误配和 SSRF 影响。
- `models.yml` 保持 `UMask=0077` 和服务账户权限；发布标记只存 SHA-256。
- 行级重写必须保留注释、其他条目、Key 以外的字段；YAML 特殊字符走安全引号。
- 一键重启只调用现有受保护的重启接口；不自动重启。
- 模型名上限和字符集只做基本校验；真正的“这个模型存在”由获取列表和用户选择保证。

## 验证

- provider API：Key 不回显；留空保持；填写替换；base URL 校验；注释/其他条目保留；发布失败不写；未发布 409；restart_required。
- upstream API：假 `httpx` 成功列表、超时、非 200、坏 JSON、超大响应、无 Key 使用已保存 Key；Key 不出现在响应/日志。
- 模型保存：首选/次选、无次选、同名提示、swaps 保留、下一条回复生效（状态 mtime）。
- echo-test 和 relay 条目名不出现在 GET/页面；`.模型` 指令测试保持通过。
- 页面：获取列表、保存提供商、重启提示、保存模型、未发布锁定；桌面和 390px 移动端无横向溢出。
- 生产部署后：面板拉到当前上游列表，选中 `deepseek-v4.1-flash-eco`，保存模型不重启；改 base URL/Key 后按提示重启一次并确认真实回复。
