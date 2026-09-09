# P115 — 能力总索引与逐项整理

**状态：active。** 先完成现有与计划能力的分类，再按本卡逐项整理；不是全部重写，也不同时启动所有计划。
**核对基线：** 2026-09-06，盘点时 `main` 为 `fe182a1`；执行期间并发治理提交推进到 `a587156`，保留其变更及本轮已存在的未提交修改。
**请求：** 把功能分成容易记住的职责，包含计划中的模块；使用 P 卡索引，整理一项就标记一项，后续不靠会话记忆重新开工。

本卡是这次整理的唯一执行清单。已有能力的契约仍以
[module registry](../../../governance/module_registry.json)／[SYSTEM_MAP](../../SYSTEM_MAP.md) 为准；
已有工作仍回到原 P 卡，不复制验收。总入口由
[work registry](../../../governance/work_registry.json) 生成到 [planning INDEX](INDEX.md)。
本轮完成的子项保留 `[x]` 和结果，不删行；整张卡结束时按仓库规则退出 live registry，记录由 Git 保留。

## 1. 先记住四块

| 工作领域 | 回答什么问题 | 输入 → 输出 | 不负责 |
| --- | --- | --- | --- |
| **资料与任务｜读** | 项目要做什么，哪些资料与条件有关？ | 任务书、原文、图片、项目情境 → 空间需求、可定位资料、条件与判断依据 | 自动把资料中的每句话变成硬约束；直接生成建筑 |
| **方案与建模｜做** | 如何形成或修改这个方案？ | 设计意图、当前候选、保留条件 → 可继续修改的候选模型 | 正式发布；把几何生成成功当作设计正确 |
| **分析与校核｜验** | 这个方案表现如何，有什么问题？ | 指定模型、工况、检查问题 → 指标、发现与建议 | 默认改变设计；替设计师批准方案 |
| **表达与出图｜出** | 如何把指定方案清楚地表达和交付？ | 指定模型、视图／表达用途 → 图纸、图像或模型文件 | 另建一份设计真相；把截图当施工图 |

这不是四道必须依次通过的关卡，也不对应四排工具栏。前期分析可以横跨“读、做、验”；
修改节点几何属于“做”，把已有节点表达成图属于“出”。Agent 与工作台按意图组合调用，
共享底座承担版本、计算、文件和正式发布。

**产品工作流归属（2026-09-08）。** MonkeyArch 是 3D 建模与空间修改工作流；MonkeyDiagram 是
平行的图纸与图解工作流，承接下文已有和计划中的出图、图纸编辑、PDF／图片批注、文字尺寸、
家具与节点表达、排版和导出。两者通过明确来源联动，共用 ArchFlow 底座；长期目录分为
`archflow/`、`monkeyarch/`、`monkeydiagram/`，真实文件迁移沿既有 owner 和调用链执行。
当前文件到目标归属、依赖方向与迁移顺序统一见 [REPO_LAYOUT](../../REPO_LAYOUT.md)。
界面采用两个同级工作区；这个方向已确定，具体入口尚未实现。详见
[ARCHITECTURE](../../ARCHITECTURE.md#parallel-user-workflows-monkeyarch-and-monkeydiagram) 和
[MonkeyDiagram 方案](../../DRAWING_MODULE_ARCHITECTURE_PLAN.md)。

## 2. 现有功能清单

“已有”表示当前生产代码存在，不自动表示真实项目试用、所有建筑场景或团队远端交付已验收。
处理方式分为：**保留**（继续复用）、**合并**（移除重复入口，先核对调用）、**删除**（确认退役后删除）、
**待实现**（没有对应生产闭环）。先归类，不按分类搬动代码目录。

### 2.1 资料与任务

| 编号 | 能力与现状 | 处理 | 实现／工作索引 |
| --- | --- | --- | --- |
| R01 | ProgramSheet：部门、空间、面积／数量／净高／功能、邻接需求；可读 authored sheet 或从选定记录派生 | 保留，作为任务书入口 | `state.program_sheet`、`studio.program`；P115 C02 |
| R02 | 将任务书应用到 Space、Relation、Connection 等状态并生成候选；不是自动满足全部需求的 massing 求解器 | 保留；与建模共用候选链 | `application/program.py`、`state_record.py` |
| R03 | Reading、types、parameters、relations、basis/evidence refs 可承载资料与判断并进入 Agent 上下文 | 保留；来源引用不等于检索服务或已执行约束 | `state.record`、`application/intent_agent.py`；[P113](P113-evidence-ledger.md) |
| R04 | 旧 `state.program` 仍被 canonical 状态使用，与 ProgramSheet 不是两个面向人的任务书产品 | 入口合并；底层兼容表示暂留，不能整包删 | `state.program`、`state.model`；P115 C09 |

### 2.2 方案与建模

| 编号 | 能力与现状 | 处理 | 实现／工作索引 |
| --- | --- | --- | --- |
| M01 | 体量方案：加／减楼层、移动／缩放／切分体量、显式 pack；方案可测量、比较、选择并执行 | 保留；不是通用任务书生成器 | `studio.options`、`state.spatial` |
| M02 | 数值修改与 typed `EDIT_COMPONENTS`：构件、参数、关系增改删；显式 removals，保留条件与来源绑定 | 保留；数值语法只作内部工具，不作为所有建筑任务的边界 | `state.record`、`studio.intent` |
| M03 | 构件生成：墙、开口、柱列、柱头、梁、山花、prism、ring、loft、dome-cap、楼梯、窗及 declined 声明 | 保留生成器；方法逐步由任务 skill 组织，不为每种构件另建应用 | `capabilities.element_producers`、`wall_solver`、`opening_solver`；[P105](P105-classical-order-producers.md) |
| M04 | 轴网、标高、宿主、偏移、派生比例、type 继承、依赖闭包与拓扑生产 | 保留；已声明依赖的传播不等于发现正确的建筑关系 | `capabilities.reference_resolver`、`state.derivation`、`state.record` |
| M05 | 中立 Geometry Program → 编译 → OCCT exact STEP＋mesh 3DM，或明确选择 Rhino 兼容导出；Rhino 增量 patch／rebuild | 保留共同编译和执行入口；不新增第二套几何执行器 | `state.geometry_program`、`compilers.geometry`、`adapters.cad_*` |
| M06 | 已有模型对象的语义身份读取、选择与 reindex；未映射对象不能借邻近构件的参数假装可编辑 | 保留 | `capabilities.element_reindex`、`tools.reindex_project`、`studio.binding` |
| M07 | 整段楼梯与墙拱洞可以生成候选，但当前侧向通道的既有入口对齐仍有实际缺陷 | 继续修真实建筑效果，不再以“实体有效”结案 | [ARCHITECTURE](../../ARCHITECTURE.md)；[P108](P108-vibe-modeling-frontend.md) |

### 2.3 分析与校核

| 编号 | 能力与现状 | 处理 | 实现／工作索引 |
| --- | --- | --- | --- |
| A01 | 体量面积、层数、高度、比例与包络检查；计量基于当前 voxel lattice，不是精确 B-rep 算量 | 保留，并在结果中说明计量基础 | `state.massing_metrics`、`studio.options` |
| A02 | 关系检查可区分 held、violated、unchecked；有 support 等声明及测量输入 | 保留；支持高度一致不是接触面积或承载力计算 | `capabilities.relation_checks`、`relations.contracts` |
| A03 | 几何语义编译、对象／资产／接口 datum 检查、3DM 独立读回 | 保留真实错误和读回；只删除无消费者的内部包装 | `compilers.geometry`、`adapters.three_dm_inspector`；P115 C03 |
| A04 | 候选 validation、承诺／义务 validator、review readiness；Studio 部分 canonical facts 仍未接入 | 保留有效检查，补真实输入；不能用空 findings 代替要求已满足 | `validation.*`、`studio.validation`；[P110](P110-canonical-state-projection.md) |
| A05 | 阶段 closure 与独立 issue 已有；检查通过、继续候选、认可方向和正式发布仍是不同动作 | 保留，正式发布不作普通预览的前置步骤 | `state.stage_workflow`、`project.issue` |

### 2.4 表达与出图

| 编号 | 能力与现状 | 处理 | 实现／工作索引 |
| --- | --- | --- | --- |
| O01 | 读取导出回执证明的 STEP／3DM，也可明确登记完整外部 3DM 并绑定其模型来源；提供下载和视口展示 | 保留共同 artifact 入口；登记与原生导出读回证明分别显示 | `studio.artifacts`、`ThreeDmViewport.tsx`；P108 |
| O02 | run-bound 的视口 PNG capture，模型选择、高亮、视角调整与候选比较 | 保留；这些是检查／表达交互，不是正式图纸 | `routes/captures.py`、`routes/compare.py`、`web/src/viewer/` |
| O03 | `/api/state` 的 frame／volumes／closure projection | 归到模型查询，不计入平立剖出图能力 | `routes/state.py` |
| O04 | 平面、立面、剖面、正交轴测、模型关联标注、Sheet、施工图、建筑渲染与色稿 | 待实现，分别见第 3 节；截图／CAD 导出不替代它们 | [出图方案](../../DRAWING_MODULE_ARCHITECTURE_PLAN.md) |

### 2.5 Agent、工作台与共享底座

| 编号 | 能力与现状 | 处理 | 实现／工作索引 |
| --- | --- | --- | --- |
| W01 | 自然语言、点击、圈选／箭头／keep 等手势、目标／动作／范围解析、澄清与 proposal | 合并重叠路由；保留真实设计歧义，不把字段问题变成人的补填任务 | `studio.intent` 下现有应用模块；P115 C06–C08 |
| W02 | deterministic／Codex／Anthropic 等 Agent provider，类型化语义修改及来源上下文 | 保留调用边界；尚无完整“取方法→调用工具→看结果→修正”循环 | `ports.model`、`application/intent_agent.py`；P115 C08 |
| W03 | proposal／pending intent 仍有进程内状态；本地工作事项的 A／B 选项和继续起点、2D／3D 批注已可从项目记录重开 | 继续、认可和 issue 分开；不将这些恢复能力写成全部聊天／在途任务恢复 | `application/{intent,clarification,episodes,gestures}.py`；P108、P115 C04／C05 |
| W04 | 候选任务队列、进度／事件、完成结果回读；完成的 retained candidate 可重读 | 保留；任务和 proposal 的进程内信息不等于完整重启恢复或取消服务 | `studio.candidate`、`routes/candidates.py`、`application/jobs.py` |
| W05 | 精确来源：查看／继续候选、默认参考选择、跨项目／旧起点拒绝；Program／options 的 API 与客户端已贯通 sourceRunId | 保留一个来源链；旧响应不能成为新来源的操作依据，不新增项目版本库 | `studio.binding`、`studio.candidate`、`studio.program`、`studio.options`；P115 C02 |
| W06 | 工作台布局、会话、对象属性、模型、事件、候选与设置；技术细节部分已可按需展开 | 保留真实结果交互，逐步移除默认参数审批和诊断主屏；不逐功能增 toolbar | `web/src/app/`、`web/src/features/`；[P108](P108-vibe-modeling-frontend.md) |
| W07 | StateRecord、语义词汇、关系、承诺、派生值、operator；旧 developed／portfolio 表示尚被投影消费 | 保留项目事实与兼容读取；只在真实调用链合并，不另建“统一状态” | `state.*`、`semantics.*`、`relations.contracts`；P115 C09 |
| W08 | 项目定位、输入、内容寻址记录、run／branch、文件 writer、原子 HEAD 与独立 issue | 保留，其他模块只用共享项目端口 | `project.*` |
| W09 | 单一 runner、seat 范围与交接、模型调用边界、CAD host 监督 | 保留真实执行；确定性生成不应伪装成一次模型调用 | `runtime.project_runner`、`capabilities.{discipline_seats,geometry_proposal}`；P115 C10 |
| W10 | 本机／远端 API 模式、token／CORS、协议能力声明、OpenAPI 生成 SDK、启动器与环境配置 | 保留；本机 runtime 配置继续只读，UI 设置持久化另见 P114 | `studio.shell`、[PROTOCOL](../../PROTOCOL.md)、[P114](P114-user-settings-bridge.md) |
| W11 | run／verify／freeze／open／issue／reindex 命令，archcheck、devctl、CI、独立 clone 接入说明 | 保留真实入口与行为检查；不扩建清理专用检查框架 | `tools.*`、[接入指南](../../WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) |
| W12 | `labs/` 目前只有探索规则；论文、比较实验和共享工具箱是独立工作线 | 保留分工，不把试验或投稿门槛加到普通建筑修改前 | [labs](../../../labs/README.md)、[P094](P094-caadria-2027-manuscript.md) |

## 3. 已有与计划中的 P 卡对应

现有 7 张 live 卡全部列入。P115 只做总索引和本轮整理，不取代它们的功能验收。
尚无独立 P 卡的想法先以本节条目索引；确定首个实现切片及负责人后再拆卡，不把待定计划伪装成 ready。

| 能力／工作 | 当前状态与 P 索引 | 下一步及复用边界 |
| --- | --- | --- |
| 论文与比较实验 | [P094](P094-caadria-2027-manuscript.md) blocked；用户已暂停论文工作，摘要进入全文评审的通知已回填 | 独立研究线；本轮关联排程见第 5 节，不为写论文启动未授权实验 |
| 古典构件与旋转实体词汇 | [P105](P105-classical-order-producers.md) ready | 按真实缺失构件扩展 producer；当前已有楼梯／窗等，旧能力枚举需刷新；不复活逐建筑硬编码工具 |
| Studio 真实使用闭环 | [P108](P108-vibe-modeling-frontend.md) active；完整模型、图纸来源、A／B、批注恢复、界面收纳和新模型提示已交付；页面图像、完整批注与明确参照进入意图调用已在本地实现并通过隔离验收 | 视觉输入待接入现用页面并验证真实改稿效果；同一澄清保留首轮图像与修改起点，查看另一模型不改编辑来源 |
| 要求进入真实校核 | [P110](P110-canonical-state-projection.md) blocked，等待具体要求与授权来源 | 一个真实 obligation／授权 claim → 现有 validator；移除同路径空 facts，不另造 validator |
| 候选连续修改 | [P111](P111-continuing-design-cycle.md) blocked，等待当前页面与新的试用修改 仅指试用，代码与隔离验证已交付 | 不重复开发；Program／options 的已完成补齐回填 C02，真实建筑效果仍独立验收 |
| 研究证据组织、导入／导出 | [P113](P113-evidence-ledger.md) blocked，等待首个消费需求与既有表示缺口核对 | 先核现有 Reading／source refs 能否满足首个消费需求，再决定新增记录；不把私人 Atlas skill 字段写成核心的永久前提 |
| 本机用户设置持久化 | [P114](P114-user-settings-bridge.md) 本地实现与验证完成，随共享改动收尾 | 本机 API、启动器与现用面板已接通；保存前合并最新未改字段，模型默认值下次启动生效 |
| 自动任务书拆解、文档结构化 | P115 F01：计划，尚无独立实施卡 | 接到 R01／R02；输出可定位的需求和来源，歧义交给人判断，不捆成整栋自动生成 |
| 案例／规范 RAG、图像资料检索与校准 | P115 F02：计划；P113 仅覆盖证据承载，不是完整 RAG | 方法 skill 调用解析／检索工具，查询相关原文与图；复用已有项目文件和 Reading，不先建通用知识平台 |
| 墙体／开口与楼梯改稿方法 | P115 F03：方法 skill 试点；真实使用由 P108 承接 | 先解决本次通道，再用另一场景检验复用；领域方法可携带脚本，几何／引用／保存仍调用现有工具 |
| Agent 按语义调工具、查看结果、自修复 | P115 F04：计划，现有 typed 单次编译尚不等于该能力 | 先消除 C05–C08 的旧耦合；一项任务只加载相关方法和工具，不把全部 registry 塞进 prompt |
| 环境分析：日照／辐射、采光、热环境 | P115 F05：用户提出的方向，尚无生产执行 owner | 首先选一种分析，输入选定模型和工况，输出指标与发现；领域 adapter 可调用 Ladybug 类工具，不拥有设计写权 |
| 平立剖与正交轴测 | P115 F06：`runtime.drawing_elevation` 已实现 STEP 派生正交立面 SVG／PNG、项目保存及冷读回；Studio 入口、平面与剖面尚未接通 | 先把现有立面输出接到当前模型和图纸界面，再逐类扩展；复用 [既有出图方案 A](../../DRAWING_MODULE_ARCHITECTURE_PLAN.md)、CAD source、执行监督和项目文件 writer |
| 图形样式、线宽、标注、Sheet 与模型变化更新 | P115 F07：既有出图方案 B／C／E | 在真实视图输出后扩展；模型、视图、纸面排版分工，不复制模型；先核实际需要，不预造全部 schema |
| 施工图与详图、材料层、节点、表格 | P115 F08：既有出图方案 D | 需要实际模型和构造信息；表达缺口不能用虚构节点补齐，独立手绘详图注明其来源 |
| 建筑渲染、色稿、材质／光照表达 | P115 F09：用户提出的方向，尚无完整服务 | 复用选定模型与 artifact 输出；视觉候选不能静默改模型，精确模型图与生成图分清 |
| 通用结果展示、分析／图纸 artifact 接入 | P115 F10：按首个非 CAD 结果需要扩展 | 复用 ProjectArtifactRef／WorkspaceSink 与现有 artifact 入口；没有真实消费者前不建第二 runner 或万能任务总线 |
| episode／任务重启恢复、取消 | P115 F11：已知有限缺口，未作为今晚全部完工条件 | 分清完成结果可回读与进行中任务不可恢复；有实际使用需求时扩展已有 owner，不建聊天数据库 |
| 算法搜索策略／OCBA／预算分配 | P115 F12：[架构文档](../../ARCHITECTURE.md)明确暂缓，由算法方向后续研究 | 不恢复旧 controller，不当作这轮建模的前置 |
| 队友独立开发与接入 | P115 F13：已有入口、本机隔离验证及 PR #3 的远端 CI 通过；第二位成员复现待完成 | 选定源码基线 → 独立 clone／短分支 → PR／Actions／review；不在同一个检出里多人切分支，不复制维护者私有项目 |
| 模型制造准备：3D 打印缩放拆件，后续板材排料与激光／CNC | P115 F14：MonkeyFab 独立工具已本地实现闭合 STL／OBJ 等比缩放、按 X1C／H2D 参数封闭拆件及 STL／装配清单输出；ArchFlow 应用调用尚未接入 | 主归“出”，改变设计的拆件／接头回到“做”，工艺条件归“读”、制造检查归“验”；共享工具箱以 `monkeyfab-print-preparation` 登记为 `reference`，源码留在 MonkeyFab；接入时扩展真实消费 owner 和项目工件入口。板材排料、激光／CNC 仍待实现 |

## 4. Agent、skill 与工具的分工

- **方法由 skill 组织：** 判断当前任务需要什么资料、如何解释意图、选择做法、检查结果、调整方案。
  围绕“入口改造”“任务书拆解”等任务组织，不为每扇门、每个参数建一个 skill。
- **工具完成执行：** 解析、检索、构件生成、几何运算、模拟、投影、文件写入。领域方法可以携带自己的
  适配脚本；共享几何、项目状态、执行与存储不复制。
- **项目数据留在统一状态与项目文件里：** 墙／门类型、尺寸、材料、来源、关系和候选版本不是写死在 skill 的实例值。
- **运行方式确实要改变：** 读取相关方法 → 查询模型／资料 → 调用工具 → 看结果 → 必要时修正。
  只给当前单次 JSON 编译器包一份 SKILL.md 不算完成；工具字段和可修正错误在内部解决。
- **界面按任务呈现结果：** 模型或分析／图纸为主，推荐少量相关操作；参数、对象编号、诊断可按需看。
  保留直接选择、拖动和快捷操作，不要求一切只能聊天。

队友接入时先约定一条实际调用：**做什么、读哪个来源、需哪些输入、返回什么、是否写设计／仅写结果、失败如何返回**。
这是工具说明与现有 API 的内容，不新增一套跨模块总 schema。共享入口的最小约定如下：

| 接缝 | 复用什么 | 每个贡献者不得各做一份的内容 |
| --- | --- | --- |
| 指定工作来源 | ProjectBinding、StateRecord、选定 run／exact base | “最新文件”推断、私人状态库、手动填写 run ID 的 UI |
| 修改设计 | 现有 typed operator → candidate → runner | 直接覆盖 canonical、第二套候选执行器 |
| 只读分析／出图 | 选定模型及现有项目文件 writer；结果带来源 | 为了分析先造一个假建模候选，或擅自修改设计 |
| 结果交回工作台 | 现有 artifact refs／API，按实际新结果扩展 | 文件路径硬编码、每个领域各起一套下载／任务服务 |
| 模型调用与技术失败 | 现有 provider seam，内部可定位错误和有限修正 | 把 schema 错误改写成“请建筑师补参数”；无限重试 |
| 正式交付 | 独立 project.issue 与既有检查 | 把自动生成、保存或显示等同于人的认可或发布 |

## 5. 逐项整理清单

每项只在删除／改动实际落地、相关检查完成后勾选；“已盘点”“已派发”“计划中”不等于实现完成。
同一卡内保留已完成记录。未勾项按顺序取一个最小切片，不把整表当作本轮必须全部实现的项目。

- [x] **C01｜完成分类与索引。** 初次覆盖 67 个 owner、7 张原 live 卡；2026-09-08 已补齐到 69 个 owner、8 张 live 卡以及本轮明确的计划方向；按职责归组，不搬包。本卡先落盘，再开始以下整理。
- [x] **C02｜完成选定来源前后端贯通。** Program／options GET 接收 `run`，POST 接收 `sourceRunId`；option 选择与 worker 保留创建来源，无效来源不退回 WIP。客户端读写传递选定来源，只允许来源和绑定摘要均匹配的结果生成候选，旧响应不会覆盖新来源。OpenAPI SDK 和协议已同步；program／options 61 测、候选续改 5 测与客户端 13 项接线测试通过。现有项目的建筑效果仍由 P108／P111 试用核实，不重做本项实现。
- [x] **C03｜已删除无消费者的编译回执自摘要。** 移除 `GeometryCompilationReceipt.receipt_digest` 及其冻结断言；保留 `to_dict()`、编译程序身份、错误、asset substitution 的真实摘要和历史 round 读取。编译器／接口 datum／语义几何 27 测通过，两文件净删 8 行。
- [x] **C04｜已退役空转的 declared-controls 入口。** 核实无设计 successor／candidate 消费及保留记录格式后，删除 `/api/controls` 的 endpoint、store、DTO 三文件、挂载和两项专属冻结测试，并更新协议、模块索引与生成 SDK。仍供澄清解释用的 AuthoredControlDraft 保留；clarification catalog 7 测与接口生成一致性检查通过。删除源码可从 Git 恢复，无项目数据迁移。
- [x] **C05｜已解除“运行候选＝替人接受方案”的耦合。** candidate 路径只生成预览并保存既有人工判断，不再自动 accepted／rejected。显式采纳复用现有 decision 入口，必须指定同 proposal 的成功 `candidateId`，证据读取该候选的 retained StateRecord；沿已有语义关闭同起点未决定选项，保留原文理由、旧 episode 格式和读取，不移动 HEAD／issue。episodes／candidate／proposals 95 测通过、1 项真实 Villa 环境测试跳过，包含默认来源变化后的精确采纳回归。自动视觉候选与方法循环仍见 C08。
- [ ] **C06｜合并普通意图的重复分流。** 核对 route regex、target／action／scope resolver 的真实用途，移除让非 scalar 任务提前掉进数值补问的分支。保留明确数值输入、滑杆、选中目标和 keep 条件；不是删除所有 parser。
- [x] **C07｜已完成技术错误责任边界。** Agent 无效 scalar／字段／单位／element 下沉错误为 `502 INTENT_AGENT_FAILED`，不向人返回语法补问；Agent 可明确返回 `unsupported`，沿既有终止结果解释工具限制。确定性直输和真实设计澄清保留。相关四组 90 测通过，另补 scalar unsupported 分支回归。自动修正属于 C08，本项不冒充自修复已实现。
- [ ] **C08｜完成一个方法＋工具＋检查的 Agent 闭环。** 先用 F03 通道任务，不建立泛化技能平台；Agent 需要能读相关资料／视图、调用现有工具、查看候选并修正。以真实通行与后续修改验收，不以 JSON 合法或测试数量验收。
- [ ] **C09｜核对旧状态投影的合并。** `StateRecord → developed/portfolio` 与旧 program 仍有生产消费者；先列读写双方和身份计算，再折叠适配。保留历史记录读取、依赖和 exact-base，不能仅删 guard 或伪造 option_id。
- [ ] **C10｜折叠确定性 proposal 的 provider 往返。** 现有 producer 的已知结果绕 `RecordedProposalProvider`、回执和 authoring 解析；在已有 owner 直接编译并保留真实校核、seat 范围和持久化。真实模型 provider 与 retained rounds 读取保留。
- [ ] **C11｜校准过时 P 卡文字（本轮相关部分完成）。** 已更新 P111 的 Program／options 来源限制，移除 P108 的旧重建清单和“尚无候选”快照，并将重复整理清单归到本卡。2026-09-08 已同步 P094 的暂停状态、P110／P111／P113 的具体等待条件及 P114 的源码交接状态；P105 的旧能力枚举仍待实际消费者核对；本轮不关闭原功能验收，也不将尚未核对的卡标成完成。
- [ ] **C12｜队友接入交回。** 确定可分发基线，给首位成员一个明确领域切片和现有接口；第二环境复现后标完成。推送、仓库成员权限和私人资料分享由用户明确授权，不自动执行。

**今晚实现切片：C02 前端接线、C03、C04、C05、C07，以及 C11 的本轮相关回填。**
用户在清单落盘后要求完成基础设施再收工，并允许现有 Claude 任务与 5.6 Terra 分担执行。
主代理负责契约和验收；Terra 负责 C03／C04 机械删除；Claude 协作任务调用 Claude 完成 C05。
**最高完成标准是队友快速上手、并发快速迭代。** 只有减少已复现接入摩擦的改动进入今晚切片，不新增审批层或通用平台。
C06、C08–C10 与尚未实施的 F01–F13 留在后续队列，不以这轮基础整理冒充建筑方法或新业务已交付。
UI、真实建筑项目、研究等现有任务的写入边界不因总索引而改变。

**持续功能推进（2026-09-08）。** 用户要求继续实施未完成能力后，当前切片落在 P108 的完整模型与图纸批注连续使用，以及独立的 P114 用户设置。P108 包括完整资产显示、图纸到模型的明确来源、同一事项的 A／B 继续与重启恢复、3D 批注保存、后续候选保留外部设备。先完成同一入口的实际使用，再回填已验收结果；不同时启动其余研究、分析或制造计划。

**室内施工图试用（2026-09-06，c518 工作树）。** 本轮实现和检查位于独立工作树，尚未合入主检出；不改变上方整项能力的完成状态。

- **读｜R03／F01–F02：** 读图定位、单位换算、平立面对齐及缺失厚度的标准／厂家做法查证，已整理为个人 skill `rebuild-interior-from-drawings`。当前没有已实现的规范 RAG；本轮使用官方网页和厂家手册检索，将具体来源及选用理由放入项目既有 Reading，由参数的 `source_ref` 引用。
- **做｜M03–M05：** 扩展现有生成链的 `planar-surface` 表面表达，以及 `prism.elevation`。未知构造厚度保留已知表面；有厂家系统依据的板厚作为可修改候选，标高与厚度独立控制。复用既有 OCCT／runner，没有新增房间模块或几何执行器。墙地衔接沿 R03／F01–F02 查证原图材料与标准适用条件、M03–M05 用既有 `prism`／派生参数表达装修层及结构板、A02–A03 核对平面饰面和竖向标高；标准与跨度支持的板厚仅为候选，不是原结构实值。
- **验｜A02–A03：** 已执行真实候选及 STEP／3DM 读回，分别验证面板标高移动和厚度变化；同时修复无 massing 的 StateRecord 投影丢失多条来源的问题。没有声明的建筑关系仍为 unchecked，不以导出成功替代图纸对应判断。
- **出｜O01–O02：** 首轮室内候选已由 MonkeyArch 的 state／artifact API 读取，模型加载器检查通过，并生成实际模型的轴测预览。界面人工核对、其余空间与构造细化继续沿项目推进；不计为 F06–F08 的正式平立剖或施工出图能力。

本轮固定柜进一步消费了 `occt_backend` 实现并由 `cad_execution` 公开的 `project_occt_lines`／`section_occt_lines`：从真实 STEP 的 BRep 返回可见、隐藏及剖切折线，由项目消费端形成平立剖图。该结果归 M03–M05、A02–A03、F06–F08 的相关切片及 O01–O02；目前只完成本柜消费的实际验证，不代表通用自动施工图能力，也不改变上述整项状态或 checkbox。

### CAADRIA 关联排程（2026-09-08，仅纳入计划）

按论文任务转来的整改顺序承接已有 P 卡，不新增任务编号或实现框架。
权重只表示本轮论文结论影响、证据缺口与依赖程度，不表示工时、录用概率或全局产品优先级。
当前核对基准为 `D:/ARCHFLOW_V4` 的 `a5871564` 加当时 WIP；本轮未执行新论文实验。

| 顺序与归口 | 已核断点及最小范围 | 完成条件与依赖 |
| --- | --- | --- |
| **先核范围：18% 历史判断与状态充分性；C09／F11，`state.record`、`studio.intent`，论文判断归 P094** | `developed_design_view → bootstrap_developed_state` 的适配结果仍将义务、依赖、决策和转换留空；`intent_agent.record_sheet` 没有专门输入义务、承诺或 episodes。`EpisodeStore.retain/flush` 已能把判断写入匹配 run，但未附着 run 的判断可仅留在内存，已保留拒绝理由也未自动进入下一次提案 | 先列清实际下一轮输入、已恢复内容和遗漏反例；候选／验证恢复不重列开发。作者决定要论证“限定状态下续改”还是包含长期判断连续性，再决定是否需补现有输入或恢复接口；不默认实现通用记忆系统 |
| **工程首项：22% 义务／承诺接线；[P110](P110-canonical-state-projection.md)，与 P108 协调 Studio caller** | 现有 `studio.validation` 仍给规范 validator 空的 canonical 内容；字段存在和 claim 引用存在不等于要求被测量。按 P110 的现有 state／validation owner，从一条真实、有来源及授权的要求接到候选绑定与实际检查结果 | 同一要求分别验证满足、违反、未测和旧基底；违反有 finding，满足有实测证据，未测不自动注销义务。先确定要求和来源，缺项不靠造字段填补；该卡为 blocked，等待具体要求与授权来源，未启动实现 |
| **并行准备：10% 独立实体复核；A02／A03、M07／F03 与 P108，正式实验归 P094** | 复用 `adapters.three_dm_inspector`、`adapters.cad_execution`、`capabilities.relation_checks` 和 `runtime.project_runner`。明确导出实体读回与 `compiled-predicted-bounds` 的差别，仅为所选任务补缺少的测量或 checker 输入 | 用作者确认的允许变化、保持条件及容差，测目标、漏改、误改和必要接口；预测 bbox、实体合法及建筑关系分别报告。若沿楼梯／平台案例，独立核对宽度、轴向位置、标高、踏步及连接剖面，未测项保留未测；不以 8 个对象变化宣称只重算 8 项 |

10% 先复用已有 inspection 比较和 CAD adapter 内部的 STEP 冷读、形体测量与点内外判定，
无需等待 P110 全部接通。只有实际任务需要把复核结果接回 candidate 时，才在现有
runner／CAD／checker 边界补输入；导出 CAD 的 Z-up 与 checker 的 Y-up 坐标、单位必须对齐。
实测 bbox 仍不能代替形体接触或净空检查；当前楼梯参照的顶面比平台顶面低一个级高，也不能误设为两者等高。

论文负责人需交作者确定三件事：**保留的主张及 Markov 状态边界；正式精确案例、允许变化／保持条件、来源授权与容差；三条件比较的共同预算及独立复核办法。**
论文任务 2026-09-08 的定向核查已确认：候选 `studio-cand-20260906-010451-dca4de25-4e9d`
的 intent `base_state_digest` 匹配 A03 保留 runner 的 `design_state_digest`，应保留
[RESEARCH_POSITIONING](../../RESEARCH_POSITIONING.md) 现有的 A03 配对依据。
候选 StateRecord 的 `predecessor_ref` 虽指 A02，也不能仅据这一字段把 A02 称为该意图的精确输入；
A02 保留为另一次几何对照，不据此推断额外历史执行顺序。
同次只读重读 A03 的实际 `preview.3dm` 与该候选，仍为 443 个命名对象、8 个几何变化
（四楼梯与四平台）、435 不变、无增删。该核查回报、A02 几何对照及工具箱三次 A03 工程重复
分别引用，不混用执行起点或分母；确定性工程重复不替代模型比较。本轮主线未另跑实验。

25% 的正式比较由论文／实验 owner 锁定协议后另按授权执行；12% 的已有结果纠错和 7% 的方法说明可由论文整理立即并行。
P062／P063 原始记录保持不变，旧计数与候选／选择／issue 的表述由 P094 对回来源纠正。
P094 原 9/7 的“资格未知”表述也属于 12% 的立即文稿纠错，依据用户 9/8 已提供的接受通知更新；
不得继续列为等待用户提供通知。
来源协商 4% 与资源增长 2% 保留在论文关联后续范围，只在选定主张确实需要时进入对应既有 owner。
总待办已收到论文清单，本卡承接工程依赖，不重复另派。已验收的 3DM 显示修复保留完成状态；
本轮排程不重开该产品任务，也不把一般曲面 tessellation 或论文实验加入它。

## 6. 全部 owner 的覆盖索引

下表每个 owner 只列一次，按主要职责归组；一个能力可服务其他组，不能因此复制 owner。
精确 `owner_path`、API、tests 与 invariants 链接回 [SYSTEM_MAP](../../SYSTEM_MAP.md)，本卡不镜像这些字段。

| 分类 | owner ID |
| --- | --- |
| 资料与任务（3） | `state.program`、`state.program_sheet`、`studio.program` |
| 方案与建模（10） | `capabilities.element_producers`、`capabilities.geometry_proposal`、`capabilities.opening_solver`、`capabilities.reference_resolver`、`capabilities.wall_solver`、`capabilities.element_reindex`、`state.spatial`、`state.developed_design`、`state.decision_operator`、`studio.options` |
| 分析与校核（7） | `capabilities.declaration`、`capabilities.relation_checks`、`state.massing_metrics`、`validation.engine`、`validation.model`、`studio.validation`、`adapters.three_dm_inspector` |
| 表达与出图（3） | `studio.artifacts`、`adapters.drawing_svg`、`runtime.drawing_elevation` |
| Agent 与工作台（6） | `ports.model`、`capabilities.discipline_seats`、`studio.binding`、`studio.candidate`、`studio.intent`、`studio.shell` |
| 共享：状态与语义（12） | `state.commitments`、`state.derivation`、`state.design_portfolio`、`state.model`、`state.operational_state`、`state.record`、`state.stage_workflow`、`relations.contracts`、`semantics.conditions`、`semantics.registry`、`semantics.roles`、`submission.model` |
| 建模编译与共用 CAD 执行（6） | `state.geometry_program`、`compilers.geometry`、`runtime.project_runner`、`adapters.cad_execution`、`adapters.cad_patch`、`adapters.cad_program` |
| 共享：项目与版本（11） | `project.containers`、`project.digests`、`project.inputs`、`project.issue`、`project.layout`、`project.location`、`project.manifest`、`project.ports`、`project.record_kinds`、`project.refs`、`project.repository` |
| 共享：通用契约（3） | `contracts.authority`、`contracts.canonical`、`contracts.fields` |
| 实际命令入口（6） | `tools.freeze_project_stage_workflow`、`tools.open_stage_run`、`tools.run_project`、`tools.issue_project`、`tools.verify_state_record`、`tools.reindex_project` |
| 研发支撑（2） | `tools.archcheck`、`tools.devctl` |

## 7. 本卡边界与检查

- 文档：本卡、原 P111 的来源限制／简化条目、ARCHITECTURE 的入口链接、work registry 及其生成索引。
- 首个代码切片：`archflow/compilers/geometry.py` 与 `tests/test_geometry_compiler.py`。
- 今晚 API 实现沿已有 P108 的工作范围：controls 三文件、main／router 挂载、episodes／candidate／proposal decision 路由、intents／intent／intent_agent／clarification 入口与对应测试；PROTOCOL、模块 registry 和 OpenAPI 生成 SDK 由主代理统一同步。P115 只索引这些子项，不重复占有 P108 的 API 路径。
- 后续切片只有在具体目标／调用者核清后才扩展本卡 write_scope；已有 owner、既有行为测试优先。
- 不改私人项目、当前服务／浏览器／Rhino、不推送、不自动归档未知 WIP，不创建清理专用工具或元数据。
- 文档检查：链接可达、69 owner 完整对应、生成地图无漂移、scoped diff。C03 使用现有编译器、接口 datum、语义几何测试及 `tools/archcheck.py`。

**本轮收尾（2026-09-06）：** API 全套 558 passed／2 skipped；Web 30 passed／2 skipped；
编译器相关 27 测、OpenAPI 生成一致性、类型检查／生产构建、archcheck（213 files）与 diff 检查通过。
跳过的是需要额外真实项目／3DM 输入的测试；构建仍有现有大 chunk 提示。
Claude 协作任务已实际调用 Claude 完成 C05，Terra 完成机械清理、技术错误边界和来源接线；
主代理完成集成验收。改动已落在本地源码，未提交／推送，现用服务未重启，真实建筑效果未以自动测试代签。

分类借鉴了当天 Zoning Paper Method 的“每个模块回答一个不同问题、输入输出可解释”组织方式，
不复制其四模块算法流水线：[Zoning Paper §3.3](https://docs.google.com/document/d/1P-8tvrhHd8pv5DYds6DTvE7XzgVll-XxyyPfbs-5DHE/edit)。

**规则统一（2026-09-08）。** AGENTS 只管稳定工作边界，模块注册表管软件职责与接口，工作注册表管现行任务，architecture_policy 管可执行检查。独立模块从真实消费者的现有 API／适配接入；预留协议不等于已有插件加载器。已把论文暂停、校核来源未定、续改待试用、证据消费者未定改为 blocked，保留未完成验收。范围检查使用各提交当时的卡与策略，关闭卡后不倒查成越界；P000 只覆盖明列维护路径。贡献指南、接入指南、labs 与 PR 模板同步同一规则，不另加登记流程。
