# P111 — 候选续改：已交付实现与现有项目试用

**状态：** active（仅跟进现有项目试用；实现与隔离验证已交付，不在实现队列中）
**方向：** [愿景](../../VISION.md)；唯一 live 清单为 `governance/work_registry.json`。
**本次可交付范围：** 在已有候选上继续一项修改，不再误回旧起点。反馈恢复、正式发布与研究比较不捆成一次大交付。

整体职责与下一步见 [ARCHITECTURE.md](../../ARCHITECTURE.md)，benchmark 见
[RESEARCH_POSITIONING.md](../../RESEARCH_POSITIONING.md)。本卡只保留此次交付、试用边界及已记录的局部简化候选，不再承担总路线。

## 1. 先让什么工作消失

用户看着候选 A 说“接着改”，不应重新解释 A 的变化、寻找 run 编号或重做上一轮。系统要明确下一轮针对哪个模型，并从该模型继续；用户保留的部分不被悄悄改回去。

这是一个具体的软件修复，不是已经证明“下一代基础设施”。设计判断、直接建模和正常取舍不按多余操作计算。工程机制的数量不作成绩。

## 2. 修复前的核对

2026-09-04 定向源码核对；首项起点问题已由第 3 节修复。其余是当时的独立缺口，不是本卡追加实现要求。

| 发现 | 动作与边界 |
| --- | --- |
| 画面中的 `selectedCandidateId` 与下一次 intent 的编辑起点分离；`project_state(binding, run_id)` 已可读指定 run，但 intent 和 worker 仍走默认起点 | 沿 `studio.binding/intent/candidate` 传递用户明确选择的候选。默认跳过 harness 是正确边界，不改成“最新文件自动生效” |
| episode 只在内存中可读，意见等后续 run 才 flush；`record_sheet` 不含相关历史判断 | 若意见恢复成为下一项使用阻碍，在现有 owner 补保存与使用，不另建聊天数据库；本卡未实现 |
| validation 传入空 `CanonicalState(ref=head)`，现有关系检查则另行运行 | 由 P110 接入一项真实要求及其来源。检查未覆盖就明确显示，不能用空检查表示建筑正确 |
| 依赖闭包、引用拓扑生产、柱高带动柱头/梁/山花已有实现 | 直接复用。问题不等于“整个依赖层不存在”，不再造图或遍历器 |

直接入口：Studio 的 `web/src/app/App.tsx` 与 `web/src/app/useSession.ts`，API 包的
`routes/{intents,candidates,state}.py` 与 `application/candidate.py`；按 `sourceRunId`、
`source_run_id` 和 `project_state` 核对调用方，不按修复前的行号重做。

## 3. 本次实现：候选续改

已沿现有 Studio owner 接通 `sourceRunId`：画面旁明确“从此版本继续”，所选 run 的 retained record 进入 intent、proposal、排队与 worker。默认参考选择不变，浏览模型不自动换起点。旧提案按起点禁用，失败切换保留原起点，返回默认时同时加载对应模型。前端草稿和历史不清空。

临时项目的两轮修改回归已通过：A 修改基座高度，B 在 A 上修改檐口高度，B 保留两项修改，A、authored inputs 与 HEAD 均不变。真实 React 与临时 3DM 的隔离浏览器检查已通过浏览不改起点、明确续改、草稿保留、旧提案禁用、失败切换保留起点、返回默认模型六项行为；浏览器 API 为模拟，不能据此宣称真实建筑验收。任务书、体量选项生成仍仅支持默认起点；候选体量可读。旧 PROJECT.md 的别名仍遵守其显式 digest 绑定，本次不自动改绑。聚焦 API、前端测试、类型/构建、OpenAPI 一致性与 archcheck 均通过。本轮停止实现，下一步为用户重启 MonkeyArch 后在已有项目试用。

1. 在现有界面明确选择“继续这个候选”；用可读名称呈现起点，内部标识由系统取得。
2. 显示、intent、proposal 与 worker 使用同一个候选的 retained StateRecord。复用现有 operator 和 repository；不建立第二个项目当前版本。
3. 用已有构件完成两次有区别的修改，第二次保留第一次结果。用户无需重新描述上一轮；显示的模型与实际修改起点一致。
4. 检查本次修改和已有保护条件，保留旧基准、跨项目及外部副作用边界。不增加一次发布或审批来获得续改能力。

**能交给用户试就停：** A → 明确继续 A → B 的显示与实际模型一致，A 的变化保留，无关/受保护部分不被误改，发布位置不变。聚焦 API/UI 回归及现有 archcheck 通过后交付；不等待第二建筑、重启恢复、P110 全部完成或正式 issue。

若本次用例涉及尚不能实测的要求，结果保留具体未检查项，不据此发布或宣称建筑验收通过。P108 的首轮承诺仍按原卡核对，未完成内容不因本卡关闭而消失。

## 4. 试用与下一步的边界

用户在已有项目试用续改；实际发现的问题进入对应 owner，不因这张卡仍为 active 就重跑实现。
下一项能力方向是整段楼梯的建筑改稿，见架构文档。起点保留只是它复用的一项基础；
关系完整性、做法求解和后续接手尚需各自验证，不能由本卡的两次数值修改推定成立。

视图不足时使用[已有出图方案](../../DRAWING_MODULE_ARCHITECTURE_PLAN.md)中所需的一张图；
文件沿[现有工作环境指南](../../WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md)落到既定外部项目根。
本卡不生成新的建筑，也不发布项目版本。

## 5. 当前可简化的内部迂回

以下是源码审计发现的候选，不是已完成删除，也不是续改的强制前置：

| 现存迂回 | 简化方向与必须保留的行为 |
| --- | --- |
| `project_runner.py:577–610` 将确定性 `produce_rows` 结果包装为 `RecordedProposalProvider` 的调用回执，再经 authoring 循环解析编译 | 在现有 `geometry_proposal` owner 让已知值直接走编译与语义检查。保留真实 provider 路径、seat 范围、完整性校验和程序持久化；这不是一次真实 LLM 调用 |
| `state_record.py:1151–1207` 为 StateRecord 拼造旧 topology/portfolio/revision 投影，可选 `option_id` 又成为必需项；去掉现有 portico fixture 的 option 可复现拒绝 | 沿 `state.record` 的现有投影折叠适配；保留组件、依赖、义务和已有绑定身份。`state_digest` 依赖此视图，不能直接删 guard、换摘要或填假方案名 |
| `geometry.py:355–386` 的编译回执自摘要只有冻结测试使用，同时构造过程复制并自比对 program digest | 先删无生产调用的自摘要；再随调用链折叠。回执 `to_dict()` 仍被持久 round 记录消费，不能把整个回执声称为死代码；编译程序身份和旧数据读取保留 |

这些问题证明有局部过度包装，不证明所有回执、检查和哈希都无用。只为计数下降而删除、增加“防过度工程化”检查器，均不能解决上述使用问题。

**交付状态：** 第 3 节的实现与隔离检查已完成并交回试用。后续建筑效果和真实项目使用尚未验收，
不追加机制来等待它们；已有 P108/P110 的未完成内容仍由各自工作项负责。
