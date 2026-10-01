Closes #___ <!-- Issue 仍开着时写 Refs #___ -->

<!-- 一段话写行为：谁现在能做什么、不再需要做什么；仍剩的验收或限制也写在这里。 -->

## 工作归属

- Claim：<`GH-<issue>` 或 `GH-<issue>/<lane>`；未登记 claim 时写明本次只改共享或 unclaimed 路径>
- 分支 / base：<本任务的独立分支；实际基线提交，若因上游合入而更新，写该依赖>
- 依赖与重叠：<`python tools/governance/devctl.py work` 的结果；无重叠写无，有则说明缩窄或 blocked/depends_on 顺序>
- Reviewer / handoff：<实际审查人和交接顺序；未指定则明说>

## 位置（Placement）

新增或搬动的路径，各写它依据的布局规则（`docs/architecture/repository-layout.md` 的节或 policy 的键），与 Issue 上的放置表一致：

| 路径 | 规则 |
| --- | --- |
| `<新增或搬动的路径>` | <例如：第 6 节，只测本包的测试在包自己的 `tests/`> |

只为接线而改：<文件>。未改动：<明确不碰的部分>。

## 验证

只写实际跑过的检查与结果；没跑或不适用的写明原因。纯文档改动检查链接、命令和 scoped diff。

- `python tools/governance/archcheck.py`：<结果>
- `python tools/governance/archcheck.py --changed <PR-base>`：<结果>
- 受影响的行为测试、`api:check`、typecheck／build：<命令与结果>

## 贡献许可

- [ ] 我已阅读并同意 [`CLA.md`](../CLA.md)，并确认我有权按该协议提交本次贡献；第三方材料及其许可已明确标注。
