# Proxmox VE 集群状态卡

## 目标

给超级管理员新增 `查看集群状态` 命令，出一张与「服务器状态卡」同一套浅色面板风格的图片，展示 Proxmox VE 的节点、访客（VM / LXC）与存储。数据全部从现有 Grafana 读取，不直连 PVE API，不新增密钥文件。集群相关设置放在管理面板「服务器状态」页内。

## 已确认的现状

开发服务器上的 `data/statuscard.json` 已配置：

- Grafana：`http://192.0.2.241:3000`
- Datasource：InfluxDB（Flux），UID `bfdl7jw89tjb4b`，bucket `proxmox`
- 现有「Grafana CPU 温度」使用同一个 token（保存在 `data/status_grafana_secrets.json`）

实际查询到的 InfluxDB measurement：

| measurement | 用途 | 关键字段 / 标签 |
|---|---|---|
| `cpustat` | 节点 CPU / 负载 | `cpu`, `cpus`, `avg1`, `avg5`, `avg15`；`object=nodes`，`host=节点名` |
| `memory` | 节点内存 / SWAP | `memused`, `memtotal`, `swapused`, `swaptotal`；`object=nodes`，`host=节点名` |
| `system` (`object=nodes`) | 节点运行时长 | `uptime`；`host=节点名` |
| `system` (`object=qemu/lxc`) | 访客 | `name`, `status`, `cpu`, `cpus`, `mem`, `maxmem`, `uptime`；`nodename=节点`，`vmid`，`host=访客名` |
| `system` (`object=storages`) | 存储 | `used`, `total`, `avail`, `content`, `active`；`host=存储名`，`type=存储类型`，`nodename=节点` |
| `hwmon` | 节点 CPU 温度（可选） | `temp_celsius`；`chip_name=k10temp`，`label=Tctl`，`host=节点名` |

当前数据：1 个节点 `pve`（16 核）、5 个运行中访客（2 VM + 3 LXC）、3 个存储。Grafana 只对运行中的访客持续采样；已停止的访客通常不会出现。

## 非目标

- 不写 PVE，不做开关机、迁移等操作。
- 不做集群状态的定时推送、告警或日报；只在管理员主动执行命令时出图。
- 不做网络速率（用户未选择；Influx 里是累计字节，需要额外状态才能算速率）。
- 不直接支持 Prometheus / pve-exporter 查询模板；当前 Grafana 数据源是 InfluxDB Flux，按实际 schema 固定查询。
- 不新增已停止访客列表；只展示 Grafana 窗口内有采样的访客。

## 方案选择

采用「固定 Flux 查询 + 面板少量开关」：

- 依据实际 `proxmox` bucket 的 measurement/字段写 5 条固定 Flux 查询（节点 CPU、节点内存、节点运行时长、访客、存储；温度按开关追加第 6 条），通过 Grafana `/api/ds/query` 一次批量发出。
- 面板只暴露启用、标题、bucket、数据源 UID 覆盖、时间窗口、节点筛选和温度开关；不把整段查询语句交给用户编辑。
- 备选「读取 Grafana dashboard 面板 JSON 再执行其查询」依赖 dashboard 结构，改面板就坏，且解析开销大；备选「每段查询都可配置」失败模式太多，不适合当前只有一个数据源的情况。

## 配置

`data/statuscard.json` 新增 `cluster` 段（旧文件缺字段由 `status_config.merge` 自动补齐）：

```json
{
  "cluster": {
    "enabled": false,
    "title": "Proxmox VE",
    "bucket": "proxmox",
    "datasource_uid": "",
    "window_minutes": 10,
    "node_filter": [],
    "show_temperature": true,
    "cache_seconds": 15
  }
}
```

- `datasource_uid` 为空时沿用 `grafana.datasource_uid`。
- Grafana 地址、超时和 API Key 全部沿用 `grafana` 段 / `data/status_grafana_secrets.json`。
- `bucket` 只允许 `[A-Za-z0-9_.-]`，防止把用户输入拼进 Flux 语句。
- `node_filter` 为空表示全部节点；非空时按 `host` 精确匹配。
- `cache_seconds` 只影响机器人进程的重复查询；面板「测试查询」永远绕开缓存。

`status_config.EDITABLE['cluster']` 允许面板修改：`enabled`, `title`, `bucket`, `datasource_uid`, `window_minutes`, `node_filter`, `show_temperature`。`public_view` 返回同样的字段，不回显 token。

## 数据层

新模块 `nonebot/status_cluster.py`，不 import nonebot：

1. `build_queries(bucket, window_minutes, *, include_temperature)` 生成 5～6 条 Flux，refId 为 A～F。
2. `normalize(results)` 把 Grafana 返回的 frames 展平成记录并按标签分组：
   - 节点：`host` 为键，合并 `cpustat`、`memory`、`system.nodes`、`hwmon`。
   - 访客：`(object, vmid)` 为键，`nodename` 为节点，`host` 为访客名。
   - 存储：`(nodename, host)` 为键。
3. `build_view(raw, *, title, source, now)` 格式化：
   - 百分比用 `status_logic.percent_level` 的三档配色。
   - 字节、速率、时长、百分比分别复用 `status_logic.format_bytes` / `format_duration` / `format_pct`。
   - 访客 CPU 按 PVE 的 `cpu` 字段乘 100 展示，并标注分配核数；内存展示 `used / total`。
   - 节点顺序按名称排序；访客按节点、类型（VM 在前）、vmid 排序；存储按节点、名称排序。
4. `summary_text(view)` 给出图失败时的文字兜底。

`status_grafana.py` 增加通用的 `query_flux_batch(config, queries, *, token='', root=None, now=None, timeout=None)`：

- 复用 `_base_url`、`_request_json` 的地址校验和错误处理。
- 一次 POST `/api/ds/query`，body 的 `queries` 数组带上每个 refId。
- 返回 `{refId: frames}`；保留 `GrafanaError` 和 `last_error()` 的现有语义。
- 不改变现有 `fetch_temp`、`fetch_datasources` 的行为。

数据窗口默认 10 分钟，查询尾部 `|> last()`。某个 refId 没有数据只让对应区块为空，不让整张卡失败；Grafana 请求失败、地址/UID/bucket 非法时抛 `GrafanaError`。

## 渲染

`status_image.py` 增加 `build_cluster_html(view)` 与 `render_cluster_card_pages(view, *, width=1170)`：

- 复用现有 `STYLE` 的浅紫底、白色面板、粉色强调色和 `.resources` / `.disk` / `.stats` 等类。
- 顶部为紧凑面板，不放角色立绘：左侧 `PROXMOX VE / CLUSTER STATUS`，右侧生成时间；标题下是一行摘要（节点数、访客运行数/总数、存储数）。
- 节点区：每个节点一张面板，三个资源环（CPU、内存、SWAP）；CPU 环内可显示温度，下方显示核数、负载、运行时长。
- 访客区：按节点分组的表格，列为访客（名称 + `#vmid`）、类型（VM/LXC）、状态、CPU、内存、运行时长；每 12 行分页。
- 存储区：每行显示存储名、类型/内容、使用率条、已用/总量、百分比；每 12 行分页。
- 没有访客或存储时显示「没有数据」，不省略区块。
- 沿用 1170 px 基准宽度与 760～2880 px 的输出缩放。

## 命令接线

`nonebot/plugins/statuscard.py`：

- `COMMANDS` 增加 `查看集群状态`；`.help` 的「服务器状态」分组增加一行，说明标注「仅主人」。
- `handle_command` 增加分支。
- `send_cluster_card`：
  - 非超级管理员直接返回（沿用 `_is_admin`）。
  - `cluster.enabled` 为 false 时回复「集群状态卡未启用，请在面板「服务器状态 → Proxmox VE 集群」开启」。
  - 使用独立的冷却键 `cluster:<session>`，默认沿用 `display.cooldown_seconds`。
  - `asyncio.to_thread` 调用 `status_cluster.fetch`，避免 Grafana 请求阻塞事件循环。
  - 出图失败或查询失败时回退 `summary_text`；查询失败文本包含 `status_grafana.last_error()`。
  - 图片发送沿用 `card_delivery.image_message`。

## 管理面板

在 `nonebot/dashboard.html` 的「服务器状态」区块内、现有「Grafana CPU 温度」之后增加「Proxmox VE 集群」小节：

- 启用（下拉）
- 卡片标题（文本）
- Bucket（文本，默认 `proxmox`）
- Datasource UID（文本，留空沿用上方 Grafana）
- 数据窗口（数字，分钟）
- 节点筛选（文本，逗号分隔，留空全部）
- CPU 温度（下拉，开/关）
- 「测试集群查询」按钮 + 结果提示

`dashboard.py`：

- `/api/status/overview` 的 `config` 已由 `public_view` 返回，自动带 `cluster`。
- 新增 `POST /api/status/cluster/test`：接受未保存的 `cluster` 和 `grafana` 字段，合并后强制 `enabled=True`，调用 `status_cluster.fetch`，返回 `{ok, title, summary, nodes, guests, storages}` 或 `{ok:false, error}`。
- 现有 `POST /api/status/config` 通过 `EDITABLE` 白名单保存 `cluster`，无需新接口。

## 测试

- `nonebot/tests/test_status_cluster.py`：
  - 查询生成包含 bucket、时间窗口、字段过滤；非法 bucket 被拒。
  - 多 frame / 多标签解析：节点 CPU/内存/运行时长合并，访客按 vmid 合并，存储按名称合并。
  - 节点筛选、空数据、缺失温度、非 running 状态。
  - 摘要文本与视图格式化。
- `test_status_image.py`：集群卡 PNG、宽度夹取、只有节点/只有访客/空数据不崩。
- `test_statuscard_wiring.py`：命令规则、`cluster.enabled` 关闭时的提示、查询失败回退文本、独立冷却。
- `test_status_panel.py`：`cluster` 默认值/白名单保存、测试接口走 mock Grafana、bucket 非法返回错误。
- `test_help_coverage.py`：`COMMANDS` 自动覆盖新命令；分组和「仅主人」说明沿用现有断言。
- 更新 `docs/OPERATIONS.md` 8.5 的命令表、配置项和故障排查。

## 验证

1. 在开发服务器上用真实 Grafana 执行 `status_cluster.fetch`，确认拿到 1 个节点、5 个访客、3 个存储。
2. 生成并检查 PNG：默认宽度、窄版、无访客、无存储、无温度。
3. 启动/访问面板，测试「Proxmox VE 集群」保存与「测试集群查询」。
4. 在机器人进程里用测试事件调用 `查看集群状态`（不实际发群），确认图片发送路径与文字回退。
5. 运行相关 pytest 文件。

## 风险

- Telegraf 的 `proxmox` 输入插件字段名可能随版本变化；面板测试按钮会直接暴露查询失败，字段缺失时对应区块留空而不是伪造 0。
- 已停止访客没有采样，卡片只能显示运行中访客；在面板说明里写清。
- PVE 的 `cpu` 字段是相对单核的比例，卡片按 `×100%` 展示并标注核数，不重新归一化。
- 现有 Grafana token 必须有 `Data sources: Query` 权限；沿用现有密钥，不额外扩大权限。
