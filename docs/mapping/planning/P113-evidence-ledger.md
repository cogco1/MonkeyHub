# P113 — 证据账本:调研结果成为记录的证据词汇

**状态:** ready(前置已齐:record kind 表、ADR-006 语义注册表、villa 的五个 research run 仍可读)
**方向:** [愿景](../../VISION.md);唯一 live 清单为 `governance/work_registry.json`。
**本次可交付范围:** 一种记录、一个校验、一次导入、一个导出。Studio 页签与阶段检查不在本卡。

## 1. 先让什么问题消失

早期调研(RAG)的结果没丢——villa 的 research-001…005 五个 run 里留着 source ledger、visual-evidence-inventory
(2 张源图、7 个 ROI、7 条观察、7 个构件假设)、材料证据包,82 个内容寻址的对象在 objects/ 里——但作者记录 96 个实体
没有一个引用它们,`evidence_refs` 是自由字符串、悬空不报错,Studio 也看不到调研。设计师今天无法说"这面墙为什么在这"。

## 2. 修复前的核对

| 发现 | 动作与边界 |
| --- | --- |
| 生产这些记录的模块在退役线里(visual_inventory、stage_control_sources),已归档 | 只读它们留下的记录,不复活生产者 |
| `relationship_refs` 已按声明关系校验(2026-09-03) | evidence_refs 用同一种做法:对着账本校验,悬空即拒 |
| codex 的 `build-lineplus-research-atlas` skill 有自己的证据字段(id / mechanism / case / fact / spatial_result / url / status) | 账本字段做成它的超集,导出时逐字段映射,不让 skill 迁就内核 |

## 3. 本次实现

1. `archflow/state/evidence.py`(新 owner):`research-evidence-ledger` 记录(EvidenceLedger@1)——每条证据:id、
   kind(source / image / roi / observation / hypothesis / material)、url 或对象 ref、status(confirmed / reported /
   calculated / inferred)、aspects(existence / morphology / relative_position / topology)、supports(构件或 role,经语义注册表解析)、since。
   登记进 `record_kinds.py`。
2. `StateRecord`:`basis_refs` 里指名的账本是 evidence_refs 的词汇;记录级与实体级 evidence_refs 逐条校验;没有证据的构件
   不拒绝,报 `unsupported`(与 unchecked 关系同一做法)。
3. 导入 A:`tools/import_research_evidence.py` 从 villa 的五个 research run 生成账本进 `research-006`(Shared 容器)。
4. 导出:`tools/export_research_atlas.py` 把账本写成 skill 的 `research.source.json` + `cases.source.json` 与引用对象,
   放到 `exports/research-atlas/<日期>/`,并留一条 `research-atlas-export` 记录。图板本身由 skill 渲染。

## 4. 验收

- 一个含悬空 evidence_ref 的记录被拒绝并点名最近的证据 id;一个引用账本证据的记录通过。
- villa:research-006 的账本条数与五个 run 的源条数对得上;导出目录能被 skill 的 `validate_research_data.py` 通过。
- spine 与 api 套件、archcheck 通过;注册表新 owner 有测试。

**能交给用户试就停:** villa 有账本、导出能进 skill。Studio 的 Research 页签(需 codex 的文案表)与 stage 0 的
`evidence-coverage` 检查各起一张卡,不塞进本卡。
