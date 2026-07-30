# Claude 修复轮 — 总结报告（2026-07-30）

## 范围与方法

输入：同日深度审查确认的 14 项缺陷（8 维度并行审查 + 逐项对抗性验证，
22 智能体）中可作为 bug 修复的 10 项，外加基线测试暴露的 GBK 编码问题。
方法：逐项修复 → 每项配回归测试 → 全量回归 → 全部 diff 交第二轮
10 智能体对抗性复审 → 复审确认项二次返工 → 再次全量回归。

设计级发现 #3/#12/#13/#14（sandbox_gold 中立性违规、use-zone 证据捏造、
幻影审批、摆拍拒绝）**不在本轮范围**——需按 RMPA 流程立独立工作卡
（几何模板降格为 probes/p026-sandbox-gold/ 项目记录，模型获得真实拓扑
提案自由度）。

## 结果总览

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 全量测试 | 357（默认 GBK 环境 1 error） | **367 全绿**（双环境） |
| 新增回归测试 | — | 10 个 |
| archcheck | PASS | PASS |
| 确认缺陷（第一轮 14 项中的 bug 类） | 10 项开放 | 10 项修复 |
| 复审对补丁的确认缺陷 | — | 6 项全部处理 |

sandbox_gold.py（P026 核心，2075 行）从"从未执行、两条终点必崩"变为
**有端到端测试驱动全管线**：概念→硬门拒绝→修订→几何编译→确定性实现→
体素派生→五视图渲染→硬验证→审批→承诺监控→CAS 晋升→重载→重启重载。

## 修复清单（最终状态）

| # | 内容 | 关键文件 |
| --- | --- | --- |
| F01 | devctl 链路强制 UTF-8（GBK 环境兼容） | tools/devctl.py, tests/test_devctl.py |
| F02 | 回执先于 CAS 构建 + P024 artifacts-only 显式门 | archflow/commit/committer.py |
| F03 | 验证回执/提交回执绑定提交内容摘要（构造+比对+留档） | submission/model.py, validation/{model,engine}.py, commit/{model,committer}.py |
| F04 | 承诺门三重判定：id + 内容摘要 + outcome；共享状态集常量 | validation/commitments.py, commit/committer.py |
| F05 | HEAD CAS 跨进程文件锁；initialize 同锁防 TOCTOU | project/repository.py |
| F06 | paused_from/effective_status 封堵 pause 洗白 | runtime/player_control.py |
| F07 | readiness 刷新对齐校验器（invalidated:* + 不动点迭代） | state/decision_operator.py |
| F08 | mesh 真实射线奇偶采样 + 顶点/面数显式上界 | realization/sandbox.py |
| F09 | check-scope 无参恒真形式改为报错 | tools/devctl.py, governance/work_registry.json |
| F10 | sandbox_gold 两条必崩路径修复 + 端到端 Gold 测试 | runtime/sandbox_gold.py, tests/integration/test_sandbox_gold.py |

## 对抗性复审结论（第二轮）

四个分区审查员的总判定：**全部修复关闭了其声称的漏洞**（对 F02 的
走私 delta、F05 的双进程竞态、F06 的全部洗白变体、F08 的两万点属性
测试等均做了基线 vs 修复后的可执行对照）。复审确认了 6 项补丁自身的
缺陷（0 项误报），已全部返工处理，明细见工作日志"复审返工"节。

## 建议后续工作（需项目所有者决策 / 立卡）

1. **P026 设计级返工卡**：sandbox_gold 的几何模板降格为项目输入记录、
   use-zone 真实空间推导、真实审批事件、真实拒绝——审查发现
   #3/#12/#13/#14。这是 P026 能否诚实关闭的前提。
2. **git init（或文件清单追踪）**：write_scope 与完成回执目前无法核对
   实际改动文件，devctl 的范围治理缺乏事实来源；这也是本轮唯一
   未根治的确认发现（F09 残余）。
3. **41 项第一轮未送验发现**：中危项（36 张归档卡无回执、Pantheon
   golden-only 测试、剖面图与立面图相同、world_recovery 信任状态标签、
   控制器崩溃窗口卡死分支等）值得排入后续轮次。
4. **防火墙结构性规则**：给 P045 增加如"`GeometryProgramProposal`
   只允许测试/探针/指定作者能力构造"的结构性检查，替代字符串黑名单
   追加。

## 文件清单

改动 15 个源文件、10 个测试文件，新增 1 个测试文件
（tests/integration/test_sandbox_gold.py）与 1 处注册表命令修正
（P001 verification 显式路径）。基线快照与完整 diff 在会话
scratchpad（`baseline/`、`clean-diff.patch`），会话结束后不保留——
如需持久 diff 请尽快 git init 后提交本轮改动。

相关文档：
- [修复计划](2026-07-30-bugfix-round-plan.md)
- [逐项工作日志](2026-07-30-bugfix-worklog.md)
