## GitHub 修改与合并

本节适用于所有 Agent（包括子 Agent）对 GitHub 仓库的修改，包括代码、文档、配置和工作流。

1. **先建分支，再改文件。** 确认远端主线后，从最新主线创建并切换到 `feature/<任务名>`，或按仓库约定创建并切换到 `develop`。已有任务分支可继续使用，但先确认当前分支和任务一致；共享 `develop` 已包含无关变更时，另建任务分支。不得在主线直接编辑、提交或推送。
2. **验证请求，再提交审核。** 逐项核对用户要求，检查差异只包含本任务，运行相关验证。验证失败或无法运行时，如实记录原因，不请求合并。
3. **通过 Pull Request 请求合并。** 验证满足要求后，只推送工作分支，从该分支向远端主线开 PR，写明变更、验证结果和已知限制，并向用户提供 PR 链接。PR 目标以远端主线为准：使用 `master` 的仓库目标为 `master`；本仓库当前目标为 `main`。不要为此新建或重命名主线。
4. **等审核，不自行合并。** 由维护者在审核和必需检查通过后合并。不得绕过分支保护、强制推送主线或用本地合并后推送替代 PR。若无权限推送或创建 PR，保留工作分支并报告阻塞。

## Agent skills

### Issue tracker

GitHub Issues in `Lkyloveaw/denia-bot`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

single-context — one root `GLOSSARY.md`, with architecture decisions in `docs/adr/`. See `docs/agents/domain.md`.
