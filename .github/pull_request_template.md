## 工作归属

- GitHub Issue：#___
- Work id / lane：<新任务填 Issue；继续既有 legacy 工作时填 `P###[/lane]`>
- 分支 / worktree：<本任务的独立分支和检出>
- Base ref：<本次实际基线提交；若因上游合入而更新，写该依赖>
- Contributor / reviewer / handoff：<实际责任人、审查人和交接顺序；未指定则明说>
- 依赖与 active overlap：<`python tools/devctl.py work` 的结果；无重叠写无，有则说明缩窄或 blocked/depends_on 顺序>

<!--
新任务规则：GitHub Issue 是 canonical task identity，PR 是实现/review 单元。
P/M/R 工作卡编号已冻结，不再分配 P116+ 或新的 M/R 编号；既有 legacy 卡原地收尾。
work_registry 只负责当前 source-edit scope / 并发 / handoff，不是第二份 backlog。
Issue-native GH-<n> 的机器 claim 正在 #60 迁移；迁移完成前，现有 archcheck 仍接受历史 P### claim。
P000-governance 仅适用于 checker 已列定的治理维护路径，不是通用越界许可。
-->

## 贡献许可

- [ ] 我已阅读并同意 [`CLA.md`](../CLA.md)，并确认我有权按该协议提交本次贡献；第三方材料及其许可已明确标注。

## 写入范围

- [ ] 新工作已绑定 GitHub Issue；继续 legacy 工作则明确原卡/lane。
- [ ] 需要源码并发协调时，已在 `work_registry` 登记窄 `write_scope`、branch/worktree、依赖与 handoff。
- [ ] 改动落在本任务的 `write_scope` 与 policy 的 `shared_write_scope` 内，或属于 `P000-governance` 的有限范围。
- [ ] staged diff 只包含本次修改，已处理当前重叠与依赖。
- [ ] 若软件归口、公开契约或列出的测试改变，已同步现有 module registry；内部修复不要求改表。

改到的路径：

```text
<git diff --name-only <PR-base>...HEAD 的结果，或明确路径清单>
```

## 验证

按本次影响选择检查，只勾实际完成项；未跑或不适用的写明原因。纯文档检查链接、命令和 scoped diff，不要求跑业务套件。

- [ ] 受影响行为测试：<命令与结果>
- [ ] `python tools/archcheck.py` — 当前文件树的静态边界
- [ ] `python tools/archcheck.py --changed <PR-base>` — 已提交源码范围
- [ ] API／DTO 改动：<相关路由检查与 `api:check` 结果>
- [ ] Web 改动：<交互检查、typecheck／build 结果>
- [ ] 文档或手动核验：<实际检查内容与结果>

## 请 reviewer 重点看

- <哪个文件 / 哪个判断最值得质疑>
- <独立功能如何调用现有接口；确实变化的软件契约、写入或校核边界>
- <仍影响使用的限制，以及需要继续的具体任务>