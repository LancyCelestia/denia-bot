# 卡片排版改版

日期：2026-11-22（Asia/Shanghai）。分支：`feature/card-layout-overhaul`，基于 `main`（`691fac4`），29 个文件（20 个修改、9 个新增，含本文与 CHANGELOG）。

本轮只改卡片排版和渲染服务的分页逻辑，不改数据采集、推送逻辑和发送顺序。帮助菜单（`help_image.py`、`.help` 命令）按要求暂不改动。本文写成时改动尚未合入，也未部署；下文的出图都通过 SSH 隧道连到 `10.0.0.226:3100` 的现网渲染容器完成，用的是容器里的 HYSongYunLangHeiW / Noto 字体（该容器仍是旧版 `server.js`，新分页规则的出图见「验证」）。

## 结论

| 卡片 | 主要问题 | 改法 |
| --- | --- | --- |
| 天气实况 / 预报 | 一行文字一个白框；预报三天各占一框，冷暖要逐行读 | 实况以 156px 温度为主体；预报做成一张表，温度区间条三天共用同一刻度，标题下自动写一句结论 |
| 群话题 | 借用天气模板，10 个词 10 个白框 | 独立模板 `group_topics_image.py`，排行条形图，标题直接写结论 |
| Epic | 没有封面；领取长链接印在图里 | 从 `keyImages` 取封面并内嵌；链接只放在随附文字（插件原本就会发） |
| 灾害 B 卡（地震/台风/海啸） | 印章压住字段表；示意图与数据无关，还印着“生产接地图瓦片”之类的开发占位文字；台风路径写死成往东北走；字号 10–14px；固定 1200 高度会悄悄裁掉长正文 | 印章与标题同一行；去掉假示意图，改为三个关键数字；台风按真实经纬度画当前→预报位置；最小 16px；改走 `/render-card`，高度随内容 |
| 链接解析预览卡 | 转发引用框被切成几段、多层时层层套框；2 列图片先缩成 350px 再放大约 1.9 倍；九宫格行距与列距不一致；长正文从句子中间断开 | 连续引用框，用左侧竖条数表示层级；网格按格子实际像素居中裁切；整组图片一个块，行列距都是 8px；按段落/句号分块 |
| 服务器状态卡 | CPU 型号长时把内存、SWAP 卡挤窄；“60m 均 xx℃”是 12px 小字挤在环里；磁盘路径从尾部截断；网络图没有时间范围 | 三张资源卡等宽；均温移到环下说明行；路径列加宽到 300px 并中间省略；峰值行加起止时间 |
| 日报 / 周报 | 同一章节在同一页里被拆成好几块且都标“· 续”；表格各列等宽，事件描述被挤；CPU 折线按峰值缩放，64% 看起来像满载 | 每章一块面板，超出单页预算才拆；数字列右对齐不换行，余宽给长文本列；百分比固定 0–100% 纵轴并标时间轴 |
| 所有分页卡片 | 页脚可能被单独挤到下一页；末页可能只剩一行，配上最小高度变成 200px 的小图；续页没有页眉 | 渲染服务分页：页脚跟随上一块、末页过短时回挪、续页加“标签 · 第 2/3 页” |

台风路径是本轮出图时才发现的：测试样例里桦加沙的预报位置在当前位置西边约 1,300 km，旧卡却画成往东北移动，方向完全相反。

## 公共约定

- **配色**：天气、群话题、Epic 改用状态卡 D「浅色泡泡」的配色（`card_theme.py`），样式限定在 `.sheet.light` 下，不影响其他模板。所有改动过的文字色在对应底色上对比度 ≥ 4.5:1；进度条、色轨等图形颜色不变，文字改用同色系深一档（状态卡 `TEXT_COLORS`，灾害卡 `_INK`）。
- **字重**：新模板只用 400。HYSongYunLangHeiW 没有粗体，600/700 会被 Chromium 合成伪粗体。
- **文本分块**：`card_document.split_text()` 优先在换行处切，其次在句末标点、逗号处，都没有才硬切。`paragraphs()` 改用它，天气卡的旧文字模式也一起受益。
- **网格裁切**：`card_document.image_uri(..., crop=True)` 按目标格子居中裁切，小原图不放大。默认行为（等比缩进框内）不变，状态卡、Epic 不受影响。

## 接口与行为变化

| 位置 | 变化 | 兼容性 |
| --- | --- | --- |
| `weather_service.fetch_view()` | 新增，返回结构化视图；`format_text(view)` 输出文字回退 | `query()` 保留，输出文字与改版前逐字一致（有测试锁定） |
| `weather_image.render_pages()` | 传入 dict 按实况/预报排版 | 旧的纯文字输入照常可用 |
| `epic_free_logic.normalize()` | 新增 `cover_url` 字段 | 只增不减 |
| `epic_free_service.fetch_covers()` | 新增：只请求 `https://*.epicgames.com`，不跟随跳转，单张 ≤ 6 MiB、仅接受 `image/*`，最多 8 张、整体 6 秒；失败的封面直接不显示 | 下载失败不影响出卡 |
| `render_client.render_html(..., whole_card=False)` | 新增参数；为 True 时走 `/render-card` | 默认值保持原行为 |
| `disaster_render.render_bulletin()` | 地震/台风/海啸走 `/render-card`；气象预警卡仍走 `/render` 固定高度 | 气象卡模板未改 |
| `card_document.document(..., page_label='')` | 新增参数，写入 `data-page-label` | 默认不写 |
| `server.js` `/render-pages` | 分页抽成 `paginateCard()` 并导出；新增 footer 跟随、`data-keep-with-prev`、末页回挪、`data-page-label` 续页页眉（插入元素带 `data-render-continued`） | 不带新属性的旧模板只会得到更合理的分页，不会多出页眉 |
| 日报默认页脚、`/状态卡诊断` 版本行 | “本地 Pillow 渲染”改为“本机容器渲染” | 只是文案 |

`server.js` 的分页规则详见 `docker/playwright-renderer/README.md`。末页回挪的阈值是单页高度的 1/4，且不会让末页比上一页更高；最多 12 页、20M 总像素的上限不变。

## 验证

**新增测试**（全部为纯 HTML 断言，不需要渲染服务），共 53 项，均通过：

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `test_card_layouts.py` | 19 | 天气视图、文字回退不变、群话题、Epic 封面白名单/大小/超时、请求体不超 2 MiB |
| `test_disaster_bulletin_layout.py` | 13 | 印章在标题行、无占位文字、字号下限、台风真实坐标与方向、JMA 来源、整卡截图 |
| `test_parser_card_layout.py` | 9 | 文本分块、裁切尺寸、连续引用框与层级竖条、转发深度上限、网格尺寸 |
| `test_status_card_layout.py` | 9 | 路径省略、资源卡等宽、均温移出环外、时间窗、对比度、日报分块、固定纵轴、表格对齐 |
| `test_page_labels.py` | 3 | 续页标签的声明与转义 |

**现有测试**：`test_weather_service`（3）、`test_epic_free_logic`（16）、`test_epic_free_service`（13）全部通过；`test_disaster_render` 16 项中 15 项通过，另 1 项需要写文件，在受限环境里跑不了，其断言（机器人改名后卡片跟着变）已另行核对；`test_render_client` 除一项需要改环境变量外均通过；`test_status_image` 中不依赖渲染服务的断言逐项核对未受影响。

**尚未运行**，需要在本机或部署机上补跑：

- 依赖 NoneBot 的接线测试：`test_weather_plugin`、`test_group_topics_wiring`、`test_epic_free_plugin`、`test_xhs_media` 等。其中 `test_xhs_media` 的核心断言（原图未下完时卡片先用预览图）已用同样方式单独验证。
- 需要 `QQBOT_RENDER_URL` 的真实出图测试：`test_weather_image`、`test_epic_free_image`、`test_status_image`。
- `server.test.js`（Node）。新增的真实浏览器用例所用数字已先在 Chrome 中核对：页脚跟随时两页高 600 / 471，末页回挪时 600 / 420，单页 340。

**出图核对**：每类卡片用固定的压力样例（长地名、长挂载点、10 个进程、多层转发、11 张图等），改版前后各出一组图人工检查。解析卡另按 `tools/diagnostics/check_parser_layout.js` 的规则在浏览器里检查页眉位置、溢出和网格尺寸，均通过。分页切点是在 Windows 版 Chrome 里算的，容器内字体的行高可能让切点略有不同；每一页的截图本身来自容器。

新增 `tools/diagnostics/preview_simple_cards.py`：用固定样例把天气、群话题、Epic 卡出图到 `nonebot/.tmp/card-preview/`，不发 QQ、不碰机器人数据，`--real-covers` 会下载真实 Epic 封面。

## 合入与部署

合入走 PR（目标 `main`），由维护者审核与合并。本机验证：

```powershell
cd nonebot
python -m pytest tests -q
node --test ..\docker\playwright-renderer\server.test.js
```

本次改动包含 `docker/playwright-renderer/server.js`，渲染容器需要重新构建才会生效：

```bash
cd /opt/qqbot && git pull --ff-only
cd docker/playwright-renderer && docker compose build && docker compose up -d
curl --fail http://127.0.0.1:3100/health
```

机器人和容器的部署先后不限：旧容器会忽略 `data-page-label`；新容器配旧代码时，页脚跟随和末页回挪照常生效，只是没有续页页眉。

## 遗留问题

| 问题 | 说明 |
| --- | --- |
| 预先存在的测试失败 | `test_status_image.ClusterCardTest.test_html_contains_nodes_guests_and_storages` 断言 `PROXMOX VE / CLUSTER STATUS`，模板实际输出 `<机器人名> / PROXMOX VE`。改版前的代码同样过不了，需决定改测试还是改模板 |
| 和风天气字段未核实 | 实况卡的湿度、风、能见度按几种常见字段名兼容读取，取不到就不显示；需用一次真实 v1 响应确认能否取到 |
| 天气预报天数说法不一致 | 帮助写“今明两天”，代码请求 `days:3`；卡片按实际返回的天数显示，帮助文案未改 |
| Epic 封面的服务器连通性 | 封面在本机浏览器里能下载；部署机能否访问 `cdn1.epicgames.com` 未验证，访问不到时卡片不带封面 |
| 解析卡请求体变大 | 2 列网格按 368px 实际尺寸内嵌，约为原来两倍；样例最大 354 KB，离 2 MiB 上限尚远，超限时按原逻辑退回文字和原媒体 |
| 帮助菜单 | 未改。命令越多列数越多，图宽 2–3k px，手机上字号偏小 |
| 清晰度 | 渲染服务仍是 `deviceScaleFactor:1`；状态卡输出宽度超过 1170 时仍由 Pillow 放大 |
| 气象预警卡 | 未改。`card_height()` 按每行 30 字估算，实际约 36 字，长正文时页脚上方会有留白 |
| B 站动态推送卡 | 仍是 Pillow 绘制（`bilibili/render.py`），不在这九类之内，风格与解析卡不一致 |
| gsuid 图卡 | EndUID 补丁固定 `/render` 1000×2400，内容不满时有空白尾巴；XutheringWavesUID 远程请求是否带宽高未核实 |
| 文档样张损坏 | `docs/` 下 34 张 PNG 的文件头被做了换行转换（`\r\n` → `\n`），无法打开；需加 `.gitattributes`（`*.png binary` 等）并重新提交 |
