# 表情包核心功能部署记录

日期：2026-11-09。目标：开发服务器 `192.0.2.226`，项目目录 `/opt/qqbot`。

服务器已有独立的面板修改；以服务器原版 `dashboard.py` 和 `dashboard.html` 为基础应用本次表情包补丁，未覆盖其余改动。安装前核对原文件 SHA-256，备份至 `/opt/qqbot/.deploy-backup-meme-20261109-132923`；未改动 `.env`、模型配置、人格配置或数据目录。新增 `nonebot/meme_logic.py` 和 `nonebot/plugins/meme.py`，安装后四个文件的 SHA-256 与暂存文件一致。

在服务器隔离副本中使用服务器 Python 3.12.3 和 MuiceBot 1.1.5 执行表情包逻辑、面板和聊天接线测试：15 passed，1 个测试环境未注册 adapter 的警告。安装后重启 `qqbot`、`qqbot-dashboard`，两者均 active；机器人健康接口显示 ready、connected 均为 true；日志显示表情包插件及相关钩子加载成功。面板主页包含表情包入口，未登录请求 `/api/memes` 返回 401。未发送 QQ 消息，也未在生产图库添加图片。

回退时先核对线上四个文件未被后续修改；从备份恢复两个面板文件，移除本次新增的两个代码文件，再重启机器人和面板。保留 `nonebot/data/memes/`，不要删除用户上传的图片。若线上文件有后续修改，应只撤销本次改动。

## 主人私聊学习扩展

同日部署主人私聊学习扩展：`meme_logic.py`、`meme_learning.py`、`plugins/meme.py`、`plugins/meme_learn.py`。先核对线上原代码和暂存文件哈希，并以 SQLite backup API 备份现有图库；备份目录 `/opt/qqbot/.deploy-backup-meme-learning-20261109-135643`。部署不修改主人身份、模型网关或聊天模型配置；视觉模型默认使用用户选择的 `deepseek-v4.1-flash`，通过独立请求头提供网关所需的会话标识。

服务器隔离副本 29 项相关测试通过，另使用临时生成的图片实际调用视觉模型和 Embedding 服务，成功识别、向量化和入隔离库。线上两项服务重启后 active，机器人 ready/connected 为 true，`meme_learn` 加载成功，图库 quick_check 为 ok，学习默认关闭。未发送真实 QQ 测试消息；QQ 商城表情的实际下载能力取决于协议端返回的图片信息。

## 私聊无回复故障修复

同日机器人发生流式调用递归：表情插件先包装 `Muice.ask_stream`，随后模型切换插件再次安装自身包装器，并把表情包装器保存为原函数，形成循环；反复尝试备用模型产生大量递归错误，内存攀升并使事件循环及 SSH 失去响应。修复表情包装器保留原函数元数据并检查包装链，覆盖两种初始化顺序；回归测试在修复前复现 RecursionError，修复后通过。修改前代码备份在 `/opt/qqbot/.deploy-backup-meme-recursion-20261109-145524`。

另查明 Gemini 首选的测试请求超时，原 DeepSeek 备用请求缺少网关所需 `x-opencode-session` 时返回 400。新增 `gateway_session` 插件，只对已配置 `relay` 网关的聊天请求添加按会话隔离的匿名路由标识，并保留已有请求头；独立模拟测试和真实流式请求验证成功。按用户先前指定的 `deepseek-v4.1-flash` 将其设为聊天首选，原 Gemini 选择保留为备用。原首选配置备份在 `/opt/qqbot/.deploy-backup-chat-gateway-20261109-182137`。用户实际私聊测试确认收到回复；随后检查机器人和面板均运行正常、QQ 已连接、图库完整性检查通过，24 张图片保留，2 个识别失败任务保留供重试。
