# SnowLuma 协议端（Docker）

SnowLuma 通过 ptrace 把 hook 注入运行中的 QQ 客户端，把 QQ 会话转成 OneBot v11。
Linux 上官方只推荐 Docker 方式（镜像内置 Linux QQ + Xvfb + VNC + noVNC）。

生产入口是仓库根目录的 `deploy-linux/install.sh` / `deploy-linux/start.sh`；本目录只保留
可跟踪的 compose 模板，真实 `compose.yml` 和 `.env.local` 由安装脚本生成，均被 Git 忽略。

## 手动启动

从仓库根目录执行（`--project-directory .` 让数据目录落到 `./snowluma`）：

```bash
cp -n docker/snowluma/.env.example docker/snowluma/.env.local
# 编辑 docker/snowluma/.env.local：至少改 VNC_PASSWD；需要内网访问再改 BIND_ADDR
docker compose --env-file docker/snowluma/.env.local \
  -f docker/snowluma/docker-compose.yml --project-directory . up -d
docker compose --env-file docker/snowluma/.env.local \
  -f docker/snowluma/docker-compose.yml --project-directory . logs | grep -E "远程桌面密码|remote desktop password"
```

## 首次接入

1. 浏览器打开 `http://<服务器IP>:6081`，用 `VNC_PASSWD` 登录 noVNC，在容器里的 QQ 上扫码登录小号。
2. 打开 `http://<服务器IP>:5099` 的 WebUI，登录凭据是启动日志里那串随机串（登录页只有一个输入框，字段名其实叫 `password`）。
3. 在 WebUI 里配置**反向 WebSocket**：
   - 地址：`ws://host.docker.internal:8080/onebot/v11/ws`（容器内访问宿主机 NoneBot）
   - Token：与 `nonebot/.env` 的 `ONEBOT_ACCESS_TOKEN` 一致
4. `docker compose ... logs -f` 看到连接成功，且 NoneBot 侧日志出现 `Bot <uin> connected`。

## 注意事项

- **只 hook 目标账号**：`SNOWLUMA_HOOK_AUTOLOAD=0` 时只恢复之前成功接入过的账号。这个开关没有 UI，只能改配置后重启。
- **注入粒度是 PID 不是 UIN**：同一客户端登录的多个号会被一起 hook，无法按号过滤。
- **数据卷不能删**：`qqbot-qq-config` / `qqbot-qq-data` 存着登录态，删了要重新扫码；换设备指纹也可能要重扫。
- **权限与网络**：`SYS_PTRACE` + `seccomp=unconfined` 是高风险配置；端口只绑内网或 `127.0.0.1`。
- 必须宿主机 `sysctl kernel.yama.ptrace_scope` ≤ 2（= 3 会直接拒绝注入）。
- 非官方方案都有封号风险：只用小号，别频繁换 IP。
