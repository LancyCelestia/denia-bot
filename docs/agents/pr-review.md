# PR review: bot reviewer

PR 的正式审查由 bot reviewer 提交，不能只存在于聊天或本地报告里。

## 身份与产物

- Review 作者：`github-actions[bot]`，由 `.github/workflows/pr-review.yml` 提交。
- 事件：`COMMENT`（不是 Approve）；人类维护者仍负责最终批准与合并，bot 的 approve 不计入门禁。
- 每次 review 同时创建 `review/bot` check run，结论为 `neutral`。
- Review 是 PR 时间线里的持久记录；修复后重新运行，旧 review 保留，新 review 对应最新 head。

## 何时运行

每个 PR 完成一轮修复、准备请求合并之前运行。评审内容至少包含：结论（可合并 / 需修改 / 仅信息）与是否阻断；Critical / Important / Suggestions 分节；每条引用 `file:line` 或 diff 位置并给出原因与建议；实际运行的验证命令与结果；未验证项单独列出。不要把未观察到的行为写成已验证。

## 运行方式

工作流位于默认分支（`main`），使用 `workflow_dispatch`：

1. 复用已有评论（推荐，正文不受 workflow input 长度限制）：

   ```bash
   gh pr comment <PR> --body-file review.md
   # 从输出 URL 末尾取 #issuecomment-<id>
   gh workflow run pr-review.yml -f pr=<PR> -f comment_id=<id>
   ```

2. 短正文直接传入：

   ```bash
   gh workflow run pr-review.yml -f pr=<PR> -f body="..."
   ```

`comment_id` 必须属于同一个 PR；工作流会校验，跨 PR 会拒绝。

## 已知限制

- 工作流必须先在默认分支；只存在于分支上的新 workflow 不能直接 dispatch。
- `github-actions[bot]` 不能预先作为 requested reviewer 添加；review 提交后才会出现在 Reviewers/时间线。
- 仓库设置 `can_approve_pull_request_reviews=false`：bot 只能提交 COMMENT，不能审批。
- `review/bot` 为 `neutral`，`gh pr checks` 可能显示 `skipping`。
- 从 `main` dispatch 给 fork PR 创建的 check run 可能不关联到该 PR（`pull_requests` 为空）；review 仍然可见。
