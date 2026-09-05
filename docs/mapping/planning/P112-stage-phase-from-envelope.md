# P112 — 阶段属于 run:developed view 从 envelope 取 phase

**状态:** ready(前置已齐:ADR-007、`tools/open_stage_run.py`、villa workflow-003 两个 stage 已闭合)
**方向:** [愿景](../../VISION.md);唯一 live 清单为 `governance/work_registry.json`。
**本次可交付范围:** 一处内核改动加一次 villa 重跑。不扩到义务、证据或发布。

## 1. 先让什么问题消失

设计师把 workflow 的 stage 0 写成 `schematic_design`,冻结成功、打开成功、跑不了:`developed_design_view` 把
`active_phase` 写死为 `design_development`(`archflow/state/state_record.py`),`StageExecutionGuard` 又要求
envelope 的 phase 与之相等。villa 的 `workflow-002 / stage-0-001` 就是这样死掉的一对;现用的 `workflow-003`
只能三个 stage 全写 design_development、靠 LOD 200/300/300 区分。阶梯表(SYSTEM_MAP "Stage ladder")因此只有一档能用。

## 2. 修复前的核对

| 发现 | 动作与边界 |
| --- | --- |
| `active_phase` 进 `DevelopedDesignState.state_digest`(绑定身份) | 允许:phase 按 run 绑,同一记录在不同 stage 的 run 有不同 state_digest,这正是 ADR-003 的"绑定身份" |
| 调用方四处:`runtime/project_runner.py`、`apps/…/adapters/harness.py`、`tools/verify_state_record.py`、Studio 的 projection | 都已持有 envelope 或 harness 的 phase;不新增参数来源,不从记录里猜 |
| retained 的 developed-design-state 记录带 `active_phase` | 只读不校验,旧 run 照旧可读;不迁移旧记录 |

## 3. 本次实现

1. `developed_design_view(record, *, run, phase, …)`:phase 由调用方给,runner 传 envelope 的 phase,harness 传自己的
   stage phase;不再有默认常量。
2. `StageExecutionGuard` 的相等检查保留(它现在检查的是"runner 传对了"而不是恒等式)。
3. villa workflow v4 冻结在 `workflow-004`:`schematic_design` 200 → `design_development` 300 →
   `candidate_coordination` 350;stage 0、1 重开重跑(带导出),closure SATISFIED、exit binding 留存;
   v3 的文件在工作区改名 `.retired`。

## 4. 验收

- `tests/test_project_runner.py`:一个 stage 0 为 schematic_design 的 workflow 能打开并跑完,closure SATISFIED;
  runner 传错 phase 被守卫拒绝。
- villa:`runs/stage-0-003`、`stage-1-003` 的 envelope phase 与 workflow v4 一致,几何与 stage-1-002 等价(worst 0.0)。
- spine 与 api 套件、archcheck 通过;retained 的旧 run 全部仍可被 Studio 读出。

**能交给用户试就停:** villa 在 v4 下能开 schematic 阶段。不顺手做 W9(PromotionDecision@2)、不碰义务与证据。
