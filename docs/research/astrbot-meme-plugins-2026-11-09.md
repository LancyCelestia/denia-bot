# AstrBot 表情包插件调研

调研日期：2026-11-09。以下为上游文档描述，未实际安装验证。

## 首选：表情包管理器

[astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) 提供 WebUI，支持新建表情包、上传图片、分类及批量整理。设置中心的使用规则可将包设为默认或绑定到会话、人格；管理页面选中的包不等于聊天使用的包。默认按分类标签提供提示，由主模型决定是否使用，无需额外选图模型；首次使用没有内置图片。语义模式先生成图片描述及向量，依据对话检索，需配置图片描述模型与向量模型并处理图片，调用可能产生费用。安装方式为 AstrBot 插件市场搜索“表情包管理器”。[来源](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/README.md)

上游当前 README（v5.3.6）要求 **AstrBot 4.28.2 或更新版本、Python 3.12 或更新版本**。另建议升级 AstrBot 前先将插件更新至 v5.2.9 或更新版本，以免旧插件与 AstrBot Desktop 核心依赖锁冲突。[来源](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/README.md)

## 其他选择

- [astrbot_plugin_emojis_tag](https://github.com/L1ke40oz/astrbot_plugin_emojis_tag)：将图片按情绪放进文件夹，插件注入标签让主模型决定是否使用，同情绪随机选择；支持 PNG、GIF 等格式，默认每轮注入提示。
- [astrbot_plugin_meme_magpie](https://github.com/Whereis-Alice/astrbot_plugin_meme_magpie)：支持 WebUI 单张上传及批量导入、视觉识别和语义及情绪匹配，也可选自动收集群聊图片并审核。需有支持图片输入的模型，向量模型可选；默认收集关闭，发送概率为 0.2。
- [astrbot_plugin_sendemojis](https://github.com/L1ke40oz/astrbot_plugin_sendemojis)：用户按情绪文件夹放图，按概率调用辅助模型判断情绪并随机选择图片，支持 `/reload_emojis` 重扫。判断失败时默认随机发送，若希望不合适就不发应检查此选项。

## 与本项目的关系

[项目 README](../../README.md) 描述当前项目基于 NoneBot2、OneBot v11 和 MuiceBot，而这些是 AstrBot 插件；不能直接将它们当作本项目 NoneBot 插件加载。线上部署情况未核实。

## NoneBot 移植可行性

调研基于上游 main 提交 [628e16f](https://github.com/anka-afk/astrbot_plugin_meme_manager/tree/628e16f00f4f3e03e0d8165cb10b161d1527aac8)，未实际移植或部署。核心功能可以适配，但需要重写框架接线：上游通过 AstrBot 的请求与回复钩子注入提示并处理结果（[main.py](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/628e16f00f4f3e03e0d8165cb10b161d1527aac8/main.py)、[事件处理](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/628e16f00f4f3e03e0d8165cb10b161d1527aac8/mixins/event_handlers.py)），其管理接口和页面也依赖 AstrBot（[web_api.py](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/628e16f00f4f3e03e0d8165cb10b161d1527aac8/mixins/web_api.py)、[ui.js](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/628e16f00f4f3e03e0d8165cb10b161d1527aac8/pages/app/shared/ui.js)）。解析和资源包等相对独立模块可以进一步评估复用，不能假定全部直接可用；语义索引及模型提供商也需要适配。

本项目已经使用 MuiceBot 的请求前和对话完成钩子（[kb.py](../../nonebot/plugins/kb.py)、[gate.py](../../nonebot/plugins/gate.py)），并在 [requirements.lock](../../requirements.lock) 固定 MuiceBot 1.1.5。该版本在流式回复中先逐块输出文字，之后运行完成钩子并发送新资源（[源码](https://github.com/Moemu/MuiceBot/blob/v1.1.5/muicebot/muice.py)）；项目样例配置启用了流式模式。若沿用文本表情标记，必须处理跨流式分块和标记泄漏，且要验证与 [model_pref.py](../../nonebot/plugins/model_pref.py) 的模型回退交互。现有 [管理面板](../../nonebot/dashboard.py) 有登录和写入校验，可在此基础上新增经过鉴权的上传与管理接口；上传验证和存储方式仍需设计。

建议先实现分类管理、用户上传、由主模型选择分类及图片发送；流式与非流式、并发会话、路径和 SnowLuma 的容器传图都需测试。按图片语义检索、自动收集、资源广场和云同步是额外范围，不等同于首版已支持。复制上游代码时须遵循其 [MIT 许可证](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/628e16f00f4f3e03e0d8165cb10b161d1527aac8/LICENSE)。
