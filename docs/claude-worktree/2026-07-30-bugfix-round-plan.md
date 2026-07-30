# Claude 修复轮 — 计划（2026-07-30）

来源：2026-07-30 深度审查（8 维度并行审查 + 14 项确认发现全部通过对抗性验证）。
本文件夹存放本轮修复的工作报告。基线快照（用于 diff 复审）：
`scratchpad/baseline`（会话临时目录，191 个文件，含 archflow/tools/tests）。

## 修复清单（按执行顺序）

| # | 目标 | 对应审查发现 | 严重度 |
| --- | --- | --- | --- |
| F01 | tests/test_devctl.py + tools/devctl.py 子进程强制 UTF-8 | 基线测试报错（GBK 环境） | 环境 |
| F02 | commit/committer.py：回执构建移到 CAS 之前 | #7 | 高 |
| F03 | validation 回执绑定提交内容摘要，committer 比对 | #6 | 高 |
| F04 | committer 承诺门与规范承诺集比对 | #8 | 高 |
| F05 | project/repository.py HEAD CAS 跨进程文件锁 + fsync | #9 | 高 |
| F06 | runtime/player_control.py 封堵 pause→cancel 绕过 | #5 | 高 |
| F07 | state/decision_operator.py readiness 支持 invalidated:* | #4 | 中 |
| F08 | realization/sandbox.py mesh 观察如实携带不确定性 | #10 | 高 |
| F09 | tools/devctl.py check-scope 去恒真化 | #11 | 高 |
| F10 | runtime/sandbox_gold.py 两条必崩路径 + 最小测试 | #1 #2 | 高 |
| F11 | 全量回归 + archcheck + 对抗性复审 + 总结 | — | — |

## 明确不在本轮范围（需要独立工作卡）

- 审查发现 #3/#12/#13/#14（sandbox_gold 中立性违规、use-zone 证据捏造、
  幻影审批策略、摆拍拒绝）：属 P026 设计级返工——几何模板应降格为
  `probes/p026-sandbox-gold/` 下的项目输入记录，模型需获得真实拓扑
  提案自由度。这超出"修 bug"范畴，需按 RMPA 流程立卡并调整 write_scope。
- 41 项未送验的低/中危发现：本轮只处理已确认项，其余留待后续轮次。

## 每项修复的验收方式

1. 修改前先读懂现有实现与相邻测试；
2. 修复 + 必要的回归测试补充；
3. 至少跑通该模块的既有测试文件；
4. 全部完成后跑全量套件（`PYTHONUTF8=1`）+ `tools/archcheck.py`；
5. 用多智能体工作流对基线 diff 做对抗性复审；
6. 每项修复在本文件夹留报告。

## 治理说明

本轮为用户直接授权的修复，未通过 devctl 立卡（注册表是机器工作状态的
唯一入口，是否补卡由项目所有者决定）。所有修改文件清单见总结报告。
