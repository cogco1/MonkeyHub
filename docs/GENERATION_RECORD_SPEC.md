# 生成过程记录规范(Generation Record Spec)

**Status:** 规范草案 v1,2026-08-29。参照外部项目
`DIGITAL FUTURE 2026 ZONING/V2_RUNTIME` 的目录纪律,落实到
ArchFlow 的 P036 内容寻址仓库。本文回答:**一次生成过程(一个
run)应当存在哪些维度的记录、如何命名、缺席时如何显式化。**

## 一、从参照项目采纳的六条纪律

1. **回执带 schema 版本**:`"schema_version": "…@1.0.0"` ——
   ArchFlow 已有(`Schema@N`),保持。
2. **阻塞回执**:外部输入缺失时写 `*_blocker_receipt.json`,
   含 `stage_reached / reason / required_to_resume / resume_artifact`
   —— ArchFlow **缺**此类型,补(维度 D9)。
3. **批量清单账本**:一行一项目、逐模块状态列 + `blocker_reason` +
   `resume_path` 的 manifest(CSV/JSON 双份)—— 即维度二的覆盖账本
   在生产中的形态;ArchFlow **缺** run 级 manifest(维度 D7)。
4. **区域分离**:config / source_staging / workspace /
   artifact_store(sha256)/ cache / temp / output / handoff /
   distribution / reference_library 各司其职;**证据永不住 temp**。
5. **内容寻址产物库**:`artifact_store/sha256/` 与 P036 同构。
6. **交付物命名**:`YYMMDD_{用途/受众}[_{形态}]`(如
   `260806_内部汇报_16x9`);发布包
   `{版本}-{YYYYMMDD}-r{轮次}-{语义槽}`。

## 二、顶层区域对照

| V2_RUNTIME | ArchFlow 对应 | 说明 |
|---|---|---|
| `config/` | provider profile / 冻结契约(卡片+registry) | 生成前冻结,digest 入 D1 |
| `source_staging/` | `probes/<p>/input/` | 原始输入,只进不改 |
| `workspace/projects/<id>/` | `probes/<p>/runs/<run>/` | 工作态 |
| `artifact_store/sha256/` | `probes/<p>/objects/` + `records/<kind>-<sha256>.json` | 内容寻址 |
| `temp/`, `cache/` | 会话 scratchpad | **禁止存证据**(见第五节缺口) |
| `output/` | `probes/<p>/exports/` | 交付物,带清单 |
| `handoff/` | D9 阻塞/移交回执 | 补 |
| `distribution/` | git tag + 卡片归档 | 版本-日期-轮次-语义槽 |
| `reference_library/` | D2 依据快照(web-evidence-snapshot) | 已有 |

## 三、一次生成过程的九个记录维度

一个 run 的记录按维度归类;**每个维度要么有记录,要么有类型化的
缺席理由**(如 `not_required`)。现有记录种类名标注为 `code`。

**D1 意图与语境** —— 为什么生成、在什么冻结条件下。
`project-bootstrap`、`production-authoring-context`、provider 身份
(id/version/fingerprint)、承诺集。缺席不允许。

**D2 依据(basis)** —— 每条外部知识的来路。
`precedent-query`(必须先声明校准哪些决策)→
`web-evidence-snapshot` → `research-invocation`(含失败)→
`research-candidates`(含 `rejected_candidates`)→
`precedent-adoption` → `decision-calibration`。
诚实空结果(零候选)也是记录,不是缺席。

**D3 决策(decisions)** —— 收敛的每一步。
`schematic-option-set` → `schematic-selection`(含理由)、
声明集(P068 契约,字段级 `source_refs` 强制)、
承诺(commitment,含权威与证据)。

**D4 生成(generation)** —— 模型做了什么。
`production-provider-invocation-*`(P053 信封,**每次尝试都留**,
含失败与超时)、`geometry-program-proposal`、
`production-geometry-program-*`(编译后程序,含 operation_order)。

**D5 实现(realization)** —— build-to-measure 基底。
`*-sandbox-scene`、`*-sandbox-realization`、`*-voxel-view`。
实现≠验收(M075)。

**D6 验收(acceptance)** —— 门与处置。
判据门记录(`*-symmetry-gate` 等,过不过都留)、
`*-validation`、`*-sandbox-archive`
(disposition ∈ accepted/rejected/repaired;
REJECTED 必引用致拒的门记录)。

**D7 度量与账本(ledger)** —— run 的自描述。
保真度测量(`*-fidelity`)、对称/判据发现
(`axial-symmetry-findings-*`)、覆盖账本(P076,待建:
有依据值/全部值、已销账关系/候选关系、已扫掠来源)、
**run manifest**(待建:一行式状态摘要,逐维度
`present / not_required / blocked`,参照批量清单)。

**D8 外化(externalization)** —— 离开中立记录的每个形态。
翻译脚本 digest、CAD 等价回执(`cad-equivalence-receipt`)、
IFC 导出回执(`ifc-export-receipt`)、渲染清单
(`*-standard-render-manifest`)。**规则:外化文件本体
(.3dm/.ifc/.png/.py)与其回执一起进 `exports/`,
回执在 `records/` 引用其 sha256;scratchpad 仅作过手。**

**D9 中断与移交(interruption & handoff)** —— 为什么停、如何续。
阻塞回执(待建,参照 `blocker_receipt`:
`stage_reached / reason / required_to_resume / resume_record_ref`)、
取代记录(typed supersession,已有)、恢复检查点
(`runs/<run>/recovery/`,已有)。

## 四、命名文法

**记录**(`records/` 内,机器侧):
`{阶段前缀?}-{角色}[-{序号}]-{sha256}.json`。
角色词取本文 D1–D9 的 code 名;新角色须先入本表。
内容寻址优先于可读性 —— 可读性由 run manifest 提供,不靠文件名。

**外化文件**(`exports/` 内,人机两用):
`{YYMMDD}_{project}_{角色}_{语义槽}.{ext}`
例:`260829_p074_cad-model_symmetric.3dm`、
`260829_p074_ifc_symmetric.ifc`、
`260829_p074_render_front-elevation.png`;
同名 `.receipt.json` 或清单记录引用其 digest。

**交付物 / 发布**(仓库外或 tag):
`{版本}-{YYYYMMDD}-r{轮次}-{语义槽}`,轮次单调递增,
语义槽说明"这轮交付了什么",不写形容词。

## 五、当前合规缺口(2026-08-29 盘点)

1. **外化文件散落 scratchpad**:`monument-semantic.3dm`、
   `monument-symmetric.3dm`、`monument-old.ifc`、
   `monument-symmetric.ifc` 与正立面/透视截图未入 `exports/`
   —— 违反 D8 规则,需迁移并补清单记录。
2. **无 run manifest(D7)**:判断一个 run 是否完备目前要 glob
   records;补一行式状态记录。
3. **无阻塞回执类型(D9)**:P066 实机失败靠 P053 信封留档,
   但"缺什么才能续"没有标准槽位。
4. **记录角色名未成表**:`p065-stage-*` 前缀与
   `production-*`、`research-*` 并存,角色词未注册;本文第四节
   即注册表的起点。
5. **覆盖账本(D7/P076)**:候选关系边枚举与销账未建。

以上 1–2 为即改项;3–5 随 P076 与 P066 深化落地。

## 六、物理落位(2026-08-29 起生效)

- **框架代码与治理**:`D:\ARCHFLOW_V4`(git)。卡片、registry、
  机制证明 probes 与代码同仓演进 —— 它们是机制证据,the test suite
  依赖其与代码的同版本性。
- **设计项目运行时工作区**:
  `D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME\`
  (非 git;区域布局见其 README)。真实建筑项目(全尺寸万神殿复原、
  实机续跑、论文实验批次)、交付物与移交包住这里。
- **绑定**:工作区 `config/workspace.json` 记录创建时框架 commit;
  每个 run 的 D1 记录须写明其运行所用框架 commit,使任何一次生成
  都能还原到确切代码版本。

## 七、依据索引(P076,派生视图)

`probes/<p>/index/basis/` 存放由 D2 记录**派生**的分片索引:
每个决策一个分片(`declaration-colonnade-bay-spacing.json`,含该决策
的全部采纳事实、引文 span、采纳/权威 id)、`_sources.json` 来源反查
(某快照被改动时恰好重开哪些事实与决策)、`_manifest.json` 注明派生
自哪些记录文件。索引可随时重建、无权威、不替代记录 —— 但改某个
决策时只读它的分片,其余不看:范围与 token 同时受限。未被任何
事实支撑的决策在分片中显式标注 `uncovered`,并进清单汇总。

### 六.一 边界修正(2026-08-29,用户裁定)

体量教训:一次四阶段重推导即向代码仓库写入数百条记录,"机制证明
与代码同仓"的前提(小而必要)会被大体量推导击穿。修正:

- **repo probes** 只收**小型**机制证明(合成 fixture、快速门演练);
  既有 probes 冻结不迁,历史证据 URI 不破坏。
- **workspace** 收设计项目与**今后一切大体量推导**——即使它们同时
  是卡片证据。
- **锚记录**:卡片证据引用 workspace URI 时,repo 内提交一条锚记录
  (digest 摘要清单)——版本控制持有指纹,本体住工作区。
- 首个住户:万神殿复原(P069)自 bootstrap 起生在
  `workspace/projects/pantheon-reconstruction/`。

## 八、落点决策表(行为规则,2026-08-30 增补)

三次落盘事故(RAG 记录滞留他项目、人读总览进全局库、预览件困于
会话暂存区)的共同根因:本规范此前只定义了"区域是什么",没有规定
"**动作发生的那一刻落哪**"。以下决策表为强制规则,对人和代理同等
适用:

| 正要产生的东西 | 唯一正确落点 |
|---|---|
| 任何 D2 记录(检索/快照/调用/采纳/校准)| **当前工作的项目** `runs/<run>/records/`;工具必须显式 `--project-id`,**禁止默认目标** |
| 外来证据 | `run_basis_import.py` 整体导入当前项目(含快照与调用信封,带出处记录)|
| 派生索引 / 执行契约 JSON / 人读总览 | 当前项目 `index/` |
| 与已有 run 一一绑定的过程预览、截图、审查件 | 当前项目 `runs/<run>/workspaces/<purpose>/`; Studio 视口截图固定为 `workspaces/studio-captures/viewport-<sha256>.png` |
| 跨项目汇报、会议分发或一次性过手副本 | 操作方显式指定的项目外分发目录; 必须随附来源 project/run/ref 清单，不能反向成为项目状态; 工具不得从 `workspace_root` 猜测一个未配置的 `output_root` |
| 证据级外化(正式 .3dm/.ifc/渲染)| 项目 `exports/` + 外化清单记录(D8)|
| 重启备份 / 大文件过手 | `V4_RUNTIME/cache/` 或 `temp/` |
| 会话 scratchpad 允许内容 | 工具结果解码缓冲、一次性诊断脚本 —— **仅此两类** |
| 项目设计文档(计划/复原表)| 项目根目录;repo 仅留指针 stub(卡片引用不断链)|
| 项目专属推导脚本 | repo `tools/`(需版本控制与测试),但其**实例参数**必须逐步外置为项目 `index/contracts/` 执行 JSON —— 代码在仓库,数值在项目 |
| 归属拿不准的任何东西 | **问用户,不自作主张** |

**RAG 三层归属**(用户裁定):全局 `reference_library/` 仅存
**未采纳**原始文献;采纳即嵌入 —— 项目内 D2 记录自含可移交;
执行层读项目 `index/contracts/`,不读代码字面量。

**执行状态**(2026-08-30):p066-live-monument 已迁工作区(锚留 repo);
万神殿复原计划已迁 pantheon 项目,权威版本在
`V4_RUNTIME\workspace\projects\pantheon-reconstruction\PLAN.md`;
repo 的 `docs/PANTHEON_RECONSTRUCTION_PLAN.md` 指针 stub 已于 2026-09-05 移出
(P069 已作为 superseded 关闭,见 `docs/CANONICAL_SPINE.md` §2.1;
stub 原文在 `ARCHFLOW_V4_ARCHIVE\20260905_repo_done-plans-and-stale-docs\`)。
`run_decision_research` 已改为 `--project-id` 必填。
