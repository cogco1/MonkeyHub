# P113 — 证据账本:调研结果成为记录的证据词汇

**状态:** blocked（先确定一个真实证据消费需求，核对现有 Reading／source refs 的缺口，再选择实现）
**方向:** [愿景](../../VISION.md);唯一 live 清单为 `governance/work_registry.json`。
**当前范围:** 先核对一个真实消费入口。下方账本、导入和导出是待复核的早期方案；只有既有 owner 无法满足具体需求时，才新增记录。Studio 页签与阶段检查不在本卡。

## 1. 先让什么问题消失

早期调研(RAG)的结果没丢——villa 的 research-001…005 五个 run 里留着 source ledger、visual-evidence-inventory
(2 张源图、7 个 ROI、7 条观察、7 个构件假设)、材料证据包,82 个内容寻址的对象在 objects/ 里——但作者记录 96 个实体
没有一个引用它们,`evidence_refs` 是自由字符串、悬空不报错,Studio 也看不到调研。设计师今天无法说"这面墙为什么在这"。

## 2. 修复前的核对

| 发现 | 动作与边界 |
| --- | --- |
| 生产这些记录的模块在退役线里(visual_inventory、stage_control_sources),已归档 | 只读它们留下的记录,不复活生产者 |
| `relationship_refs` 已按声明关系校验(2026-09-03) | evidence_refs 用同一种做法:对着账本校验,悬空即拒 |
| codex 的 `build-lineplus-research-atlas` skill 有自己的证据字段(id / mechanism / case / fact / spatial_result / url / status) | 在导出适配处映射需要的字段；私人 skill 的字段不成为核心的永久前提 |

## 3. 早期实现方案（先完成当前范围核对）

1. 若现有 Reading／source refs 的 owner 不能满足已选消费需求，再评估 `archflow/state/evidence.py`(拟议 owner):`research-evidence-ledger` 记录(EvidenceLedger@1)——每条证据:id、
   kind(source / image / roi / observation / hypothesis / material)、url 或对象 ref、status(confirmed / reported /
   calculated / inferred)、aspects(existence / morphology / relative_position / topology)、supports(构件或 role,经语义注册表解析)、since。
   登记进 `record_kinds.py`。
2. `StateRecord`:`basis_refs` 里指名的账本是 evidence_refs 的词汇;记录级与实体级 evidence_refs 逐条校验;没有证据的构件
   不拒绝,报 `unsupported`(与 unchecked 关系同一做法)。
3. 导入 A:`tools/import_research_evidence.py` 从 villa 的五个 research run 生成账本进 `research-006`(Shared 容器)。
4. 导出:`tools/export_research_atlas.py` 把账本写成 skill 的 `research.source.json` + `cases.source.json` 与引用对象,
   放到 `exports/research-atlas/<日期>/`,并留一条 `research-atlas-export` 记录。图板本身由 skill 渲染。

## 4. 验收

- 先指出真实消费入口与现有表示的缺口；若原 owner 已足够，收窄下方早期账本方案，不为满足旧清单新增记录。
- 以所选消费者能沿现有接口使用保留证据为完成条件；来源可定位，缺失支持保持明确。
- **仅采用上述账本方案时：** 一个含悬空 evidence_ref 的记录被拒绝并点名最近的证据 id;一个引用账本证据的记录通过。
- **仅采用上述导入／导出方案时：** villa:research-006 的账本条数与五个 run 的源条数对得上;导出目录能被实际消费者读取。
- 受影响行为测试与 archcheck 通过；公开契约或列出的测试改变时同步原注册表。

**能交给用户试就停:** 第一个实际消费者能使用并追溯所需证据。Studio 的 Research 页签与 stage 0 的
`evidence-coverage` 检查是独立需求；被选中实施时再复用合适工作卡，不在本卡预先创建。
