## Antigravity 向旧 Codex 派单

在已配对的 Antigravity 工作区，军师决定派单时调用 `dow-codex` MCP 的 `codex_handoff_submit`。参数 `id` 是唯一任务编号，`task` 是任务单的项目相对路径，`include` 列出所需输入文件或目录。任务单独列即可。明确列入总纲、参考代码或上一单的产物，接单会话与军师使用不同的工作区。

原会话尚未刷新 MCP 时可直接从项目根目录运行同一入口：

```sh
./handoff/dow-codex status
./handoff/dow-codex submit <编号> <任务单.md> [输入文件或目录 ...]
./handoff/dow-codex status <编号>
./handoff/dow-codex collect <编号>
```

`status` 不带编号时返回目标连接状态。派单工具只消费明确提交的请求。相同编号和内容可以安全重试，内容改变需用新编号。旧对话忙时排队。运行中的任务显示 `running`，回执首行给出 `done` 或 `blocked`。如果显示 `needs_review`，获取已有产物并核对；如果显示 `uncertain`，先核对旧对话中是否已收到该编号，避免重复派发。

通过 `codex_handoff_collect` 或 `collect` 取回回执和工作文件，保存在 `handoff/results/<编号>/receipt.md`、`handoff/results/<编号>/work/`，由军师验收。已有目录会保留。单次交接普通文件的上限为 12 MiB、1,200 个文件；沿用 Dow 凭据过滤。任务结果不会自动覆盖项目原件。
