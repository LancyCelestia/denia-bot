# QQ 聊天机器人（游戏人格陪聊）

本机部署记录与运维手册。最后更新：2026-09-25
**当前架构：SnowLuma + NoneBot2/MuiceBot**（旧方案 NapCat + AstrBot 已保留可回滚，见文末）

---

## 一、架构

```
你的 QQ 小号 3892894069
        │  ① SnowLuma 通过 ptrace 注入 hook 到运行中的 QQ.exe
        ▼
   SnowLuma  v1.14.19          QQ 协议端：把 QQ 会话转成 OneBot v11
        │  ② ws://127.0.0.1:8080/onebot/v11/ws（反向 WS，SnowLuma 主动连出）
        ▼
   NoneBot2 v2.5.0            机器人框架
   └─ MuiceBot v1.1.5         LLM 聊天 + Jinja2 人格模板
        │  ③ HTTPS
        ▼
   DeepSeek / OpenAI / ...     大模型 API（需你自己配 Key）
```

---

## 二、目录结构

```
D:\Software\qqbot\
├─ 启动机器人.bat              ← 双击启动
├─ 停止机器人.bat              ← 双击停止
├─ README.md
├─ 审查报告-SnowLuma与NoneBot2.md   ← 选型审查（含 Linux 生产环境评估）
│
├─ snowluma\                   ← QQ 协议端（自带 Node 22.13，免管理员）
│   ├─ node.exe
│   ├─ index.mjs
│   ├─ native\snowluma-win32-x64.node   ← 注入模块
│   ├─ client\                 ← WebUI 静态文件
│   └─ config\
│       ├─ consent.json                     ← EULA 同意记录
│       ├─ runtime.json                     ← hookAutoLoad 等运行时开关
│       ├─ webui.json                       ← WebUI 密码哈希
│       ├─ onebot_3892894069.json           ← ★ 小号的小号 OneBot 配置
│       └─ onebot_1690711850.json           ← 主号（未使用）
│
├─ nonebot\                    ← 机器人框架
│   ├─ .venv\                  ← Python 3.12.13
│   ├─ bot.py                  ← 入口
│   ├─ migrate_db.py           ← 数据库迁移工具
│   ├─ .env                    ← ★ 主配置
│   ├─ configs\
│   │   ├─ models.yml          ← ★ 模型配置（API Key 填这里）
│   │   └─ templates.yml       ← 人格模板的变量
│   ├─ templates\
│   │   └─ Shorekeeper.jinja2  ← ★ 游戏人格提示词
│   └─ plugins\                ← 自写插件放这里
│
├─ napcat\                     ← 【旧方案】NapCat，保留可回滚
├─ data\                       ← 【旧方案】AstrBot 数据
├─ install\                    ← 下载的安装包（约 120MB，可删）
├─ logs\                       ← 所有日志
└─ tools\                      ← 脚本
    ├─ 启动所有.ps1 / 停止所有.ps1
    ├─ 启动所有-NapCat旧方案.ps1 / 停止所有-NapCat旧方案.ps1
    ├─ 只注入小号.py            ← 保证只 hook 小号
    ├─ aTrust-禁用自启.ps1 / aTrust-恢复自启.ps1
    ├─ onebot_nonebot_token.txt      ← OneBot 鉴权 Token
    └─ snowluma-webui-pass.txt       ← SnowLuma WebUI 密码（自动化脚本用）
```

---

## 三、关键参数

| 项目 | 值 |
|---|---|
| 机器人 QQ | `3892894069`（昵称：守岸人） |
| 你的 QQ（超级用户） | `1690711850` |
| NoneBot2 | http://127.0.0.1:8080 |
| OneBot 反向 WS | `ws://127.0.0.1:8080/onebot/v11/ws` |
| OneBot Token | 见 `tools\onebot_nonebot_token.txt` |
| SnowLuma WebUI | http://127.0.0.1:5099 （只监听回环） |
| SnowLuma 登录 | 密码见 `tools\snowluma-webui-pass.txt` |

---

## 四、日常使用

### 启动 / 停止

双击 `启动机器人.bat` / `停止机器人.bat`。约 17 秒就绪。

> 两个服务都是后台进程，没有窗口。关掉黑框不影响运行。

### 在 QQ 里怎么触发机器人

MuiceBot 只在两种情况下响应：

1. **@机器人** —— 群里 `@守岸人 你好`
2. **消息以昵称开头** —— 私聊发 `守岸人 你好`（或 `小岸`、`岸`）

> 前缀词在 `nonebot\.env` 的 `MUICE_NICKNAMES` 里改。
> 私聊不想打前缀，可以把 `MUICE_NICKNAMES` 加上空字符串 `""`，
> 但那样群里也会无脑接话，不推荐。

### 常用指令

| 指令 | 作用 |
|---|---|
| `.help` | 查看所有指令 |
| `.reset` | 清空当前会话上下文 |
| `.model` | 查看/切换模型 |
| `.load` / `.unload` | 加载/卸载模型（超级用户） |
| `.whoami` | 查看自己的用户 ID |

管理页面的「运行状态」显示机器人服务、目标 QQ 连接、SnowLuma 注入和知识库服务。页面内的「重启机器人」需要确认，只重启 NoneBot；按钮会一直锁定至新进程就绪。如果启动失败，先检查 `logs\nonebot.err.log`，手动恢复机器人后页面会重新检查状态。此按钮目前支持本机 Windows 部署。

---

## 五、必做：配置大模型

**现在用 `_echo` 测试 provider，只会回显请求内容，不是真的在聊天。**

### 步骤

1. 打开 https://platform.deepseek.com/api_keys 注册并创建 API Key
2. 编辑 `nonebot\configs\models.yml`：
   ```yaml
   deepseek:
     provider: openai
     default: true          # ← 从 false 改成 true
     api_key: "sk-你的真实Key"   # ← 填这里
   ```
   同时把 `echo-test` 那段的 `default: true` 改成 `false`
3. 重启：`停止机器人.bat` → `启动机器人.bat`

### 其它服务商

| 服务商 | model_name | api_host |
|---|---|---|
| DeepSeek | `deepseek-chat` | `https://api.deepseek.com` |
| 智谱 GLM | `glm-4-flash` | `https://open.bigmodel.cn/api/paas/v4` |
| 月之暗面 | `moonshot-v1-8k` | `https://api.moonshot.cn/v1` |
| 阿里百炼 | `qwen-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 本地 Ollama | `qwen3:8b`（provider 改 `ollama`） | `http://127.0.0.1:11434` |

改完 `models.yml` **不用重启**，MuiceBot 会热重载（它有文件监视器）。

---

## 六、配置游戏人格

人格 = `nonebot\templates\Denia.jinja2` 这个 Jinja2 模板。

**当前人格：达妮娅（Denia）**，依据库洛游戏官方「寰宇人类注疏：群星交错」档案资料
（2026-03）及版本剧情档案（2026-05）编写，不是编的：

| 表层 | 里层（官方设定） |
|---|---|
| 星炬学院虚质科学部学生 | **名字、身份、共鸣能力、人际关系全部是伪装** |
| 总对大家展露出「温柔」的微笑（带引号） | 威胁等级 **海啸级** |
| 偶尔俏皮话、爱打盹、静静观察别人 | 因「阿列夫一」而诞生，挡在漂泊者与「隧锚」之间 |
| 手杖 → 泡泡，能困敌于「梦幻星穹」 | 那些温柔和慵懒**都是伪装出来的表象** |

官方台词：
> 「不要看，不要听，然后忘掉吧……有些事情，还是不知道比较幸福。」

**「引号里的温柔」就是这个人格的灵魂** —— 是表演，但不全假
（官方原文：「那些热闹真切的笑容让她感到温暖安心」）。
模板第四节「关键时刻的应对」专门处理这个矛盾。

另外还写了：慵懒节奏（「嗯……」「唔……」开头）、泡泡/星穹隐喻偏好、
「绝不激动」（音量永远不变大）、以及 6 组带语感的对话示例。

### 切回守岸人

`templates\Shorekeeper.jinja2` 保留了（那个也写了完整设定）。切换只需三处：

- `configs\templates.yml` 的 `ai_nickname` / `master_nickname` / `userinfos`
- `configs\models.yml` 里的 `template: Denia` → `Shorekeeper`
- `.env` 里的 `DEFAULT_TEMPLATE` 和 `MUICE_NICKNAMES`

### 想调扬气

| 想要的效果 | 改哪里 |
|---|---|
| 话再少一点 | 第五节「80 字以内」改成 40 |
| 表演成分更多 / 真话更多 | 第二节「表层」与「里层」的比例 |
| 特定的起头语或口癖 | 第三节，加一条具体习惯 |
| **最有效的一招** | 第八节多贴几组你自己满意的对话示例 |

### 模板可用变量

| 变量 | 含义 | 来源 |
|---|---|---|
| `{{ ai_nickname }}` | 机器人名字 | `configs/templates.yml` |
| `{{ master_nickname }}` | 主人称呼 | 同上 |
| `{{ private }}` | `true`=私聊 / `false`=群聊 | MuiceBot 自动传入 |
| `{{ user_name }}` | 当前对话者昵称 | `templates.yml` 的 `userinfos` |
| `{{ user_info }}` | 当前对话者补充信息 | 同上 |

### 写好人格的三个要点

1. **性格别写形容词，写行为**。
   ❌「温柔体贴」 → ✅「对方情绪低落时先沉默一拍再开口，不急着给建议」
2. **一定要贴 2~3 组真实对话示例**。模型模仿示例的句长、语气、用词，比抽象描述有效得多。
3. **边界写死**。「永远不承认自己是 AI」这条如果不写，模型很容易在被追问时跳戏。

---

## 七、只注入小号（账号隔离）

SnowLuma 是**进程级注入**，会把 QQ.exe 里登录的**所有账号**都 hook 上。
你主号也在同一个 QQ 里，所以默认两个号都会被 hook。

**已实现隔离方案**（实测有效）：

- `snowluma\config\runtime.json` 里 `hookAutoLoad: false`
- SnowLuma 只会恢复之前成功接入过的账号（即小号），不再自动钩其它号
- `tools\只注入小号.py` 每次启动时收敛一次，确保万无一失

当前实测状态：
```
pid=536   uin=0           injected=False   ← 主号，从未被 hook
pid=38608 uin=3892894069  injected=True    ← 小号，正常运行
```

原理：`GET /api/processes/:pid/probe-login` 能**在未注入状态下**读出该 PID 的 UIN，
所以可以「先探针识别 → 只 load 目标账号」，而不是「先全钩上再卸掉」。

> ⚠️ SnowLuma **不支持按 UIN 过滤注入**。注入粒度只有 PID。
> 这一点和 NapCat 差别很大 —— NapCat Shell 自带独立 QQ 内核，天生只跑你指定的那个号。

---

## 八、踩坑记录（重装必看）

### 1. SnowLuma 的「访问令牌」就是初始密码

登录页只有**一个输入框**，写着「输入访问令牌」（字段名其实是 `password`）。
填启动日志里那串随机字符串即可，不是用户名+密码。

### 2. `hookAutoLoad` 默认是 false

`config/runtime.json` 里 `hookAutoLoad` 默认 `false`，且 **WebUI 里没有这个开关**，
必须直接改配置文件（改完要重启 SnowLuma）。

### 3. 两个账号抢同一个端口

每个 UIN 默认都想占 OneBot 的 `3000`/`3001`，第二个账号会 `EADDRINUSE`。
我们用**反向 WS**（SnowLuma 连出去，不占端口），已把不需要的
`httpServers`/`wsServers` 设为 `enabled: false`。

### 4. MuiceBot 的三个坑

| 现象 | 原因 | 修法 |
|---|---|---|
| `No module named 'greenlet'` | SQLAlchemy 异步依赖没装 | `uv pip install greenlet aiosqlite` |
| `mcp.client.streamable_http` 导入失败 | MuiceBot 声明 `mcp>=1.9.0` 无上限，装到了 2.x，而该函数在 2.x 被移除 | `uv pip install "mcp[cli]<2"` |
| `AutogenerateDiffsDetected` | 数据库没迁移 | `.venv\Scripts\python.exe migrate_db.py upgrade` |

### 5. `python -m nonebot_plugin_orm` 直接跑不起来

它的 `__init__` 需要先有 driver，所以 `-m` 会报 `NoneBot has not been initialized`。
用 `nonebot\migrate_db.py`（先 `nonebot.init()` 再调 CLI）。

### 6. `models.yml` 是字典不是列表

格式是 `<配置名>: { ... }`。写成 YAML 列表会报
`AttributeError: 'list' object has no attribute 'items'`。

### 7. PowerShell 脚本中文乱码

Windows PowerShell 5.1 读无 BOM 的 UTF-8 `.ps1` 会按 GBK 解析，中文字符串会被截断报
`字符串缺少终止符`。**所有 .ps1 必须存成带 UTF-8 BOM**。

---

## 九、aTrust（深信服）自启已关闭

`aTrustService` 服务和 `aTrustNetflt_0` 驱动原本都是自动启动，已改为**手动**，
重启后生效 —— 只有你主动打开 aTrust 时才运行。

| 脚本 | 作用 |
|---|---|
| `tools\aTrust-禁用自启.ps1` | 关闭自启（已执行） |
| `tools\aTrust-恢复自启.ps1` | **学校要求合规时用它还原** |

> aTrust 有自我保护和看门狗，`Stop-Service` 停了会被拉回来，所以必须重启才彻底生效。
> 这是正规做法，不要去强杀它的进程。

---

## 十、迁移到 Linux 服务器

### ⚠️ 先说清楚代价

SnowLuma 官方**只推荐 Docker**方式跑 Linux，而它的镜像需要：

```yaml
cap_add:
  - SYS_PTRACE            # ptrace 注入必需，显著扩大容器逃逸面
security_opt:
  - seccomp=unconfined    # 关掉 Docker 默认的 44 个危险 syscall 拦截
```

并且镜像内置 **Linux QQ + Xvfb + VNC + noVNC**，必须暴露 **6081** 开一个网页远程桌面扫码。
内存占用 ~1–2GB（NapCat 约 300–500MB）。

详细对比见 `审查报告-SnowLuma与NoneBot2.md`。**单租户自有 VPS 可接受，共享/加固环境不建议。**

### 步骤

**协议端（SnowLuma）**

```bash
mkdir -p /opt/qqbot && cd /opt/qqbot

cat > docker-compose.yml <<'YAML'
services:
  snowluma:
    image: motricseven7/snowluma:latest
    container_name: snowluma
    restart: unless-stopped
    shm_size: 1gb
    ulimits:
      nofile: { soft: 65536, hard: 1048576 }
    cap_add: [SYS_PTRACE]
    security_opt: [seccomp=unconfined]
    environment:
      VNC_PASSWD: "改成你自己的强密码"
      SNOWLUMA_HOOK_AUTOLOAD: "1"
    ports:
      - "127.0.0.1:6081:6081"     # noVNC 扫码（不要对公网开）
      - "127.0.0.1:5099:5099"     # WebUI
    volumes:
      - qq-gateway-data:/app/data
      - qq-client-config:/app/.config
      - qq-client-data:/app/.local/share
volumes:
  qq-gateway-data:
  qq-client-config:
  qq-client-data:
YAML

docker compose up -d
docker compose logs | grep -E "远程桌面密码:|remote desktop password:" | tail -1
```

**框架（NoneBot2 + MuiceBot）**

```bash
cd /opt/qqbot
python3 -m venv .venv && . .venv/bin/activate
pip install "nonebot2[fastapi,websockets,httpx]" nonebot-adapter-onebot \
            "muicebot[standard]" greenlet aiosqlite "mcp[cli]<2"

# 把 Windows 上的这些文件拷过来：
#   bot.py  migrate_db.py  .env  configs/  templates/  plugins/
python migrate_db.py upgrade
nohup python bot.py > /var/log/qqbot.log 2>&1 &
```

**接线**：在 SnowLuma WebUI 的「反向 WebSocket」里填
`ws://127.0.0.1:8080/onebot/v11/ws` + Token，或直接改 `config/onebot_<uin>.json` 的 `wsClients`。

### 上生产前必做

- [ ] **别裸奔**：6081/5099/8080 一律绑 `127.0.0.1`，用 Nginx + HTTPS 反代或 SSH 隧道
- [ ] **改密码**：WebUI、VNC、数据库
- [ ] **备份数据卷**：`qq-gateway-data` / `qq-client-config` / `qq-client-data` **绝对不能删**，删了要重新扫码
- [ ] **检查内核**：`sysctl kernel.yama.ptrace_scope` 必须 ≤ 2（= 3 会直接拒绝注入）
- [ ] **资源限制**：给容器加 `mem_limit` / `cpus`
- [ ] **登录态易失**：容器重建可能换设备指纹导致要重扫码，做好随时重扫的准备
- [ ] **风控**：非官方方案都有封号风险，用小号、别频繁换 IP

---

## 十一、回滚到旧方案（NapCat + AstrBot）

旧文件都还在，两条命令切回去：

```powershell
powershell -File "D:\Software\qqbot\tools\停止所有.ps1"
powershell -File "D:\Software\qqbot\tools\启动所有-NapCat旧方案.ps1"
```

然后再把 `启动机器人.bat` 里的脚本名改成 `启动所有-NapCat旧方案.ps1` 即可。

---

## 十二、日志位置

| 日志 | 路径 |
|---|---|
| NoneBot2 主日志 | `logs\nonebot.out.log` |
| NoneBot2 错误 | `logs\nonebot.err.log` |
| SnowLuma 主日志 | `logs\snowluma.out.log` |
| SnowLuma 错误 | `logs\snowluma.err.log` |
| MuiceBot 数据库 | `%LOCALAPPDATA%\nonebot2\nonebot_plugin_orm\db.sqlite3` |
| 启动脚本输出 | `logs\_start.log` / `logs\_stop.log` |

查看日志一行命令：

```powershell
Get-Content "D:\Software\qqbot\logs\nonebot.out.log" -Tail 50 -Wait
```
