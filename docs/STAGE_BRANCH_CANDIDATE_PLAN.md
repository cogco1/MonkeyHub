# Stage / Branch / Candidate 工程收敛方案

2026-09-09。核对基线：`main@959a9284f0474664620510b241c7b27050c8c64f`。
这是经用户同意后实施的工程方案。2026-09-09 已完成本地实现与对应 API、OCCT、浏览器、构建和架构检查。沿用现有 P108、P111、P115 的工作归属，不另设执行清单（2026-09-24 起 P/M/R 编号冻结，后续工作以 GitHub Issue 登记，P115 为冻结的历史索引，见 #60）；当前用户项目尚未执行迁移或建立新 Stage。

**本次重构语义与调用链，保留现有建模、验证和 P036 持久化底座。第一项先修复“候选生成成功但 Studio 不更新模型”，不让这个修复等待版本系统重构。**

## 1. 固定四个概念

| 概念 | 工程含义 | 用户操作 |
| --- | --- | --- |
| Branch | 一条可持续演化的历史线，以引用指向当前 head Stage；分支可以共享早期历史 | 沿 main 继续；从历史节点另开一条要保留的未来 |
| Stage | 已接受、已提交的完整设计状态节点；内容不可原地修改 | 查看 S0、接受候选得到 S1、从历史节点继续探索 |
| Exploration | 围绕一个确切起点的一次比较或决定；复用现有 WorkingCopy | “比较两种柜体布局”；组织 A/B 和选择理由 |
| Candidate | 基于确切 Stage 的尚未提交修改，以及预览、验证和来源引用 | 生成、看、比较、调整、接受或保留备选 |

普通柜体、油烟机、材料和把手修改，不自动创建 Branch；Agent 数量也不决定 Branch 数量。

```text
main
S0 总体建筑已成立
 └─ Exploration：柜体布局
     ├─ Candidate A = S0 + ΔA
     └─ Candidate B = S0 + ΔB
         生成 → 自动预览 → 人检查 → 接受
S1 总体建筑 + 柜体 B
 └─ Exploration：油烟机
     ├─ Candidate Hood-A
     └─ Candidate Hood-B
         接受 B
S2 总体建筑 + 柜体 B + 油烟机 B

从 S0 另开持续未来时：
main          S0 → S1 → S2
cabinet-alt-A S0 → S1′ → S2′
```

Stage 编号是显示名称，不是数据库身份或全局时间排序。若从总体设计阶段完整重做，新总体方案可把第一个成立节点显示为 Stage 0，同时保留其 fork 来源；不能覆盖旧 Stage 0，也不能据编号认定两个节点相同。

两张关系图各有用途：版本父子关系回答“从哪里演化而来”；StateRecord 的建筑依赖回答“改动影响什么”。版本关系本身不判断墙、橱柜与油烟机是否相容。

## 2. 第一项：生成结果必须先显示

用户报告当天出现生成后模型迟迟不更新。当前代码存在能解释此现象的确定缺口，但尚未重放当天具体会话，因此不把它称为当天事件的唯一根因。

基线中的调用链是：

```text
Conversation 挂载
 → CandidateCard 轮询 job
 → noteJobStatus(succeeded) 添加 verdict
 → VerdictCard 挂载并请求 validation
 → noteValidation 设置 autoShowRef
 → App 自动加载候选模型
```

`Conversation` 受 `conversationOpen` 控制，默认关闭。基线中的全局 `candidate.succeeded` 事件只触发版本和文件刷新，没有接管候选完成、验证与自动预览。后台刷新还会在请求成功前标记事件已处理，并忽略读取错误，首次失败后可能等到下一事件才恢复。另有一处提前报告：App 在调用模型加载前就写入“model is on screen”。

改造位置是现有 [App](../apps/monkeyhub/web/workspaces/src/app/App.tsx)、[CandidateCard](../apps/monkeyhub/web/workspaces/src/features/conversation/cards/CandidateCard.tsx)、[VerdictCard](../apps/monkeyhub/web/workspaces/src/features/conversation/cards/VerdictCard.tsx) 和已有 viewer：

1. 把任务完成回收放在工作台常驻逻辑。复用已有事件流；事件丢失或不支持时由同一任务状态逻辑轮询、重新查询。后台读取失败时保留待刷新状态，有限重试后显示可重试错误，不能静默消费完成事件。卡片消费状态，删除原轮询副本，不再拥有生成生命周期。
2. 收到完成结果后，读取该 Candidate 的确切可视模型来源，优先其完整 composed model；不能拿全项目最后一个文件或任意 seat 导出代替完整结果。
3. 当前工作上下文仍匹配时，立即开始加载并预览。验证结果同步展示，但验证请求失败、未通过或对话关闭，都不应隐藏已经可以查看的模型；接受操作仍遵守现有有效验证边界。
4. 复用下载与 viewer 解析状态，分别显示“生成完成”“产物已就绪”“正在打开”和“候选已显示”。只有 viewer 的成功回调对应确切当前模型且有可显示几何时，才报告已显示；下载或解析失败就在视图区显示可重试错误。
5. 用户生成后已切到别的项目、Stage 或候选时，迟到结果留在原 Exploration，并在常驻结果入口提示；不抢走已选择的视图。多个并行结果先展示当前关注任务的候选，其余可直接比较，避免逐个完成就反复切屏。

验收：对话始终关闭，发起修改后仍自动看到候选；验证慢或失败时可查看已生成模型；没有渲染成功时不显示成功文案；断线恢复可找回完成结果；切走后旧响应不能覆盖当前视图。此修复单独交付。

## 3. 数据形态与明确动作

不新增 StageManager、BranchManager、CandidateManager。现有 owner 返回普通领域值，持久化仍只有 P036。

| 对象 | 最小必要信息 | 复用对象或位置 |
| --- | --- | --- |
| Stage | 唯一引用、父 Stage 引用、显示名称、完整 StateRecord 引用、完整模型来源、接受的 Candidate 与决定来源 | `state.design_portfolio` 的历史规则；P036 的不可变记录与 artifact refs |
| Branch | branch ID、可选来源 branch、fork Stage、head Stage | 重构 portfolio 的 lineage 规则；P036 内补项目级引用 |
| Exploration | ID、base Stage、已有 scope、候选引用、当前比较选择、决定记录 | `studio.intent` 的 WorkingCopy / episodes |
| Candidate | ID、base Stage、proposal 来源、已有 typed operator、影响与依赖、验证和预览产物引用 | `studio.candidate`、`StateRecordOperator`、现有 candidate run |

`operations[]` 复用 typed operator 的表达能力，不另造操作语言。影响范围与依赖由现有状态和 operator 推导；执行记录可保留当次结果，但不让 Agent 填的 `affected_entities` 成为唯一裁剪依据。验证保留可读结果引用，不把未检查简化成通过。

Candidate 的权威含义是 `base + delta`，但可在接受前物化完整 StateRecord 和模型供人检查。已有候选 run 保存完整 StateRecord，并不等于复制整个 project；不用为了“只存 delta”删除这些必要的验证、恢复和查看产物。Stage 则直接引用已物化的完整结果，回看时不要求从项目起点重放所有修改。

目前 proposal 主要在进程内；仅持久化 proposal ID 不足以恢复候选修改。因此真实生成开始时，将可重放 operator 与确切 base 一并留在该 candidate run，复用现有保存入口。无需先把全部对话和未启动任务做成持久队列。

动作严格分开：

| 动作 | 结果 | 是否推进历史 |
| --- | --- | --- |
| Generate / Preview | 生成并显示 Candidate；可以有未通过的检查 | 否 |
| Compare / Select | 改变正在比较、查看的候选；保留 A/B | 否 |
| Accept | 检查确切候选、验证和预期 branch head，建立下一 Stage | 是，原子推进目标 branch |
| Checkout | 加载历史 Stage 的模型及对应图纸 | 否 |
| Fork | 从确切 Stage 创建另一个历史线引用 | 新建 branch，不生成几何 |
| Issue | 按现有正式发布规则推进 canonical HEAD | 独立的正式发布操作 |

“接受”在 UI 上就是一次明确操作，不增加接受后再点冻结的日常双重确认。Agent 只生成 Candidate；后台验证通过也不能自行制造下一个 Stage。未选候选默认保留备选；拒绝和归档是用户可用的处置，不为三个名称额外建三套生命周期模块。

候选可继续调整而不提交中间 Stage：本轮 operator 先在父 Candidate 的确切物化结果上应用和校验，保留前一 Candidate 的来源。只有现有 typed operators 能等价表达时，才归一为原 committed Stage 上的净 delta；否则保留有序 operator 链及各步确切来源，不能直接拼接操作或改写 base digest。重放结果必须与最终物化 StateRecord 的内容身份一致。只有真实接受才出现新 Stage；不把每一轮 prompt 都计入提交历史。

## 4. 提交和持久化怎样落地

核对基线的 P036 **没有项目范围的设计 branch head API**；本次已按下述方案实现。`project.refs.BranchRef` 是 `run + branch_id + epoch` 的执行绑定，`RUN_BRANCH` 也只保存一个 run 内的分支记录。不能把它们当成已经存在的跨 run 版本库。

本次采用的具体扩展：

- Stage 的不可变接受记录，通过已有 `put_json` 写到被接受 Candidate 的 `runs/<run>/reviews/`；初始 S0 使用真实导入或初始建模 run。注册必要 record kind，引用已保存的模型与状态。
- 由 `project.layout` 定义项目内 `design/branches.json`，保存各设计 branch 的 fork/head 引用；只有 P036 repository 可写。它是设计工作引用，canonical `HEAD` 继续只表示正式发布位置。
- 在既有项目端口补设计引用的 read/list/CAS。fork 以“目标 branch 不存在”为前提插入指向历史 Stage 的条目；接受比较目标 branch 的预期 head。复用原子替换和跨进程锁的实现，不另建通用 refs 框架，也不为每个 branch 创建隐藏 run。
- 新增路径、端口及 record kind 在实现时同步到 registry、layout 文档和恢复检查；现有 `project.json` 的身份保持不可变。这里是明确的方案路径，本轮不创建或迁移真实项目数据。

提交入口以持久 Candidate ID 为目标，接收 `branchId` 和 `expectedHeadStageRef`，不依赖仍在内存中的 proposal，也不以当前屏幕的隐式选择代替提交参数。由现有 Studio 应用层协调 portfolio、验证与 P036；不另建提交服务。

提交顺序：先识别同一 `(branchId, candidateId, expectedHeadStageRef)` 已成功提交的重试并返回原 Stage；否则要求 `Candidate.baseStageRef == expectedHeadStageRef == 当前 branch.head`，再检查确切候选结果和接受资格，以该 Stage 引用为父节点留存不可变接受记录，最后原子 CAS 推进并返回新 Stage。这里比较的是设计 Stage，不是仅比较 canonical base。成功提交可用已有 Stage 的 Candidate 与父引用识别，无需增加通用幂等框架。

同一 Stage 的两个并发接受只有一个能推进 head；另一候选保留，并明确需要在新 head 上重新判断或另开分支。重复点击或请求重试返回同一已提交结果，不生成第二个 Stage。若内容写入后进程中断而 head 尚未推进，该记录不能出现在已提交历史中；重启按引用可达性和现有恢复机制处理，不自动宣布接受。

Stage 记录是模型状态快照。后续生成图纸、增加图纸修订或批注，不回写 Stage；它们是绑定该 Stage 精确模型的独立派生记录。

## 5. 多 Agent 与局部重算

计算隔离与历史分支分开。多个 Agent 读取同一不可变 Stage，各自生成 Candidate 和 speculative workspace；普通并行探索仍在同一 Branch / Exploration 下。

现有队列已经有 worker pool，缺口在范围分类和 runner 的实际执行：

1. **读与写分开。** 当前 `closure_of` 把 protected 对象也并入互斥集合。共享冻结建筑是 read/read，不应阻止 A/B 并发。write/write 或 write/read 重叠的候选在隔离空间仍可分别计算，但不能自动合并；共享外部资源另行互斥，Rhino 的单执行通道继续保留。
2. **重建与复核分开。** `StateRecord.dependency_edges` 已有 `INVALIDATES` 和 `REQUIRES_REVALIDATION`。前者确定需重新生产的内容，后者确定需重新检查的内容；不把全部依赖闭包等同于重建集合。未声明依赖无法由版本系统自动证明完整，新增构件仍须检查其真实边界条件。
3. **执行前决定复用。** 当前新 candidate 先完整跑 native seats/rows，再做 composed-model 差异合成。修改现有 runner，在生产和 CAD 执行前决定哪些源程序、形体和 datum 可复用；关系检查看到复用与变化部分的完整输入。
4. **分两步完成增量。** 先做未变 seat 的源 run 复用，再处理变化 seat 内的受影响对象。只做到 seat 级，不能宣称同一 seat 内的建筑主体已免重算。首个橱柜验收必须覆盖建筑与橱柜处于同一 seat 的情况。
5. **比较几何内容，保留真实绑定。** 现有 `cad_patch.structural_digests` 已排除部分 run/evidence 噪声；在该 owner 内结合实际输入、解析 bounds、单位、坐标系、后端和会影响几何的版本条件判断可复用性。包含 proposal 绑定的完整 `program_digest` 不能直接成为跨 run 几何内容键。旧 receipt 保留原 run，新执行通过源引用说明复用，不能改写旧回执。
6. **保留必要中间计算。** 当前 patch 闭包会把被读取对象的 producer 也纳入重建。只有验证过的源 shape / 中间输入已可读取时才跳过；缺少必要中间形体则补算相应派生，不直接砍掉闭包。重新装配完整 STEP/3DM 可以发生，不能把装配导出次数当作几何重新生成次数。

同基底的独立修改若要同时采用，先把两份 delta 合成一个 Candidate，检查对象、参数及依赖冲突，复核修改边界，再预览和接受。普通橱柜 A/B 是替代选择，不自动相加；没有必要为了它们实现通用 branch merge。真正分支合并等出现实际用户需求时再沿同一 owner 扩展。

## 6. 历史查看、续改与整体重做

Checkout 旧 Stage 只读。若从当前 head 做局部墙体修改，保留完整设计上下文，检查受影响橱柜、油烟机及其他依赖，接受后推进当前 Branch。

若保留现有未来，从旧 S0 另行研究柜体 A 或整体重做，则 fork 一个真实 Branch。新总体方案的 Stage 0 可重新定义总体状态；旧方案的下游成果不能无条件搬过去，必须在新条件下重新验证并作为候选重用。

基线中的 `projection.require_actionable` 会拒绝 canonical HEAD 已推进后的旧 run。改造不能简单取消这一检查：为**明确的 Stage 来源**建立可操作投影，验证其 Stage、StateRecord、模型与来源绑定，再在新 candidate run 中执行。其历史 canonical base 仍可保留，不能仅重贴当前 HEAD 标签就声称已适配当前发布版本。默认旧 API 的确切来源检查在消费者迁移前保留。

设计 branch 可以继续探索旧发布基底；正式 issue 仍要求当前 canonical exact-base。若要发布旧分支结果，需先明确对齐当前发布基底并重新检查相关差异。Stage fork 本身不授予正式发布权。

## 7. 3D、2D 与界面只用一个当前上下文

由现有 App 持有当前 branch、Stage、Exploration 及可选 Candidate；模型与图纸从这一来源推导。保留 VersionsStrip 作为唯一版本入口，不另建一排 Stage/Branch/Candidate 管理面板。

- 正常打开项目默认当前 Branch 的 head Stage。恢复明确选中的候选时标识“未提交”，不把最新文件当成最新 Stage。
- 切历史 Stage 时，3D、匹配的 2D、选中对象与提交基准一起切换。候选预览与继续编辑都针对该候选的实际状态；来源尚未就绪时不能向旧基准误提交。
- 图纸生成完成后自动进入 2D 并打开响应给出的确切文件与页。复用 `monkeydiagram/drawing_elevation.py` 已有 STEP→SVG/PNG 和冷读回，先接一张真实立面到 Studio；平面、剖面及施工图继续按既有出图计划扩展。
- 图纸修订按逻辑图纸、精确模型来源和视图条件组织。默认选择当前 Stage 匹配的最新图纸修订，不按所有 run 的时间、文件名或 SHA 排序。候选图纸保持候选标识；接受相同模型后可复用已有图纸，不篡改其原始生成回执。
- 图纸文件修订、页批注修订与模型 Stage 分开。批注提交采用确切页面和 ink revision；后续图纸不改变已提交批注的来源。
- 旧 Stage 无图纸时显示该节点暂无图纸，可从该精确模型生成；不能拿当前 Stage 的图纸填空。切换前复用现有批注 controller 完成保存，失败则保留草稿和原上下文。
- 对象树默认仅展开顶层与选中路径；歧义候选首屏给少量结果并支持展开/搜索，保留完整真实候选集；属性按需展开。提交图纸批注后保持当前工作区，取消强制打开对话。

## 8. 旧模块处置

| 当前 owner / 路径 | 决定 | 具体收敛与退出条件 |
| --- | --- | --- |
| `state.design_portfolio` | **重构现有 owner** | 保留 lineage 职责，改为完整 Stage 引用、持续 Branch 与 fork/advance 纯规则。旧 revision 固定 SchematicOption、只允许从 head fork、selected 后禁止续改，不能原样套用 |
| portfolio 的旧 SchematicOption 专用生命周期 | **退役旧写入 API，保留必要兼容** | 当前生命周期调用只见于专门测试，生产还使用 BranchRevisionRef/SelectedBranchHandoff 等类型。迁移新消费者时删除无真实用途的旧生命周期和包装测试；留下仍被编译链或保留数据消费的类型/reader，不恢复 votes/handoff 仪式 |
| `studio.intent` / WorkingCopy / episodes | **扩展为 Exploration，保留 owner** | 复用 common base、scope、A/B、选择及理由；引用 Stage/Candidate。选择条目不独立决定当前版本，接受委托统一提交入口。不能把 WorkingCopy 改名成 Branch |
| `studio.candidate` / StateRecordOperator | **扩展，保留唯一生成链** | 留存 base+delta，接完成即预览、受影响执行与确切来源；接受不再调用 runner/CAD |
| `state.record` / `state.developed_design` | **保留事实与当前编译投影** | compiler/seat 仍消费 developed_design_view。本方案不删除整个 developed_design；只有真实消费者迁走后才单独退役兼容投影 |
| `state.stage_workflow` / freeze、open CLI / Studio harness | **保留执行与正式流程用途，收紧命名边界** | 现有 stage 是预声明阶段/phase/LOD guard，freeze CLI 冻结的是 workflow 定义，并非用户模型快照。用户 Stage 不接到此 CLI。harness 退出须先有保全 exact-base、closure 与读回的实际替代调用；不列为本轮前置重写 |
| `project.refs.BranchRef` / RUN_BRANCH | **保留执行绑定** | 不用 run 内 epoch 充当用户历史；设计 Branch 的持久指针由 P036 项目级端口提供 |
| P036 repository / issue / artifact refs | **保留并作最小扩展** | 一个持久化 authority；新增设计引用 CAS，正式 canonical HEAD 与 issue 验证保持独立 |
| jobs / runner / cad_patch / cad_execution | **局部替换旧行为** | 退掉 shared protected 一律互斥、所有 successor 必跑全量、只有当前 run 才可复用、每个 seat 必须新 donor 的限制；保留首次全量生成、必要依赖生产、有效验证和旧数据读取 |
| App / 对话卡片 / VersionsStrip | **合并状态与结果入口** | 退掉卡片挂载驱动完成回收、validation 回调门控模型显示、文件列表冒充提交历史、按 3dm 数量统计版本；卡片仅展示常驻状态 |
| Stage / DocumentCanvas / QuestionCard / ComponentTree | **局部收拢** | 退掉首次 document run 锁定、documents[0] 充当当前图纸、图纸提交强制聊天、对象树和候选全量首屏展开；复用已有画布和批注草稿保存 |

本次已替换 `proposals` 原先接受后自动 supersede 同 base 其他待定 proposal 的范围假设：一个 Exploration 的选择不能作废其他 Exploration 或 Agent 的候选。未选候选按自己的明确决定留存。

## 9. 旧数据迁移

不改写旧模型、历史记录或其 digest。先由兼容读取投影分类，再为明确采用新语义的项目建立新引用；真实项目写迁移在实施时单独执行可恢复迁移。

| 旧数据 | 新含义 |
| --- | --- |
| WorkingCopy + options | Exploration + Candidate 引用；继续使用原始模型来源 |
| 普通 A/B 被称为 branch，且确有相同 base 的备选证据 | 投影为同一 Exploration 下 Candidate；保留原 branch ID 的来源映射，不按名称盲转 |
| 已存在多次连续修订并保留独立未来的 branch | 保留历史线及 fork 关系 |
| 有明确接受证据的完整模型节点 | 可映射为已提交 Stage；仍需验证模型与状态引用 |
| 只有成功导出或选中预览，没有明确接受证据 | 保持 legacy candidate/reference；不能自动标为已接受 Stage |
| 旧 stage_workflow 阶段记录 | 保留其执行阶段意义，不转成设计 Stage |

原样数据继续可读，模型和来源图纸能重开，是旧 writer 退出的必要条件。没有确认历史的项目，以用户明确接受的当前完整结果建立 main/S0；不伪造过去的接受行为。迁移应可重复执行而不产生重复分支或 Stage。

## 10. 实施顺序与验收

按可独立交付的结果推进，不把所有工作绑成一次大合并。

| 顺序 | 可交付结果 | 必须通过的行为验收 |
| --- | --- | --- |
| 0 | **生成后自动看到模型** | 对话关闭、验证延迟、SSE 断线、用户切走四种场景均正确；只有 viewer 成功才报已显示 |
| 1 | **main 上完成一次真实提交** | S0→柜体 A/B→自动预览→接受 B→S1；无需创建 Branch；A 保留；接受不重跑 CAD；候选连续调整两轮后重启可恢复且重放一致、不生成中间 Stage；重启恢复 S1；双击/并发接受不会重复提交；中断不会显示未提交记录 |
| 2 | **模型和图纸跟随同一版本** | 从 S1 生成一张现有立面并自动打开；S0/S1/Candidate 来回切时模型、图纸、批注和修改基准一致；旧图纸缺失不串用；保存失败不丢草稿 |
| 3 | **多 Agent 局部修改确实少重算** | 两个候选共享建筑可并行；改橱柜不调用未变建筑 producer/CAD 操作；同 seat 场景也成立；真实依赖变化会重算/复核；同基底独立 delta 可形成一个合并 Candidate 后接受 |
| 4 | **历史分支与兼容迁移** | 从 S0 fork 并保留 main/S1/S2；在新线继续多个 Stage；总体重做可显示新 Stage 0；旧记录/模型/图纸可读；旧 canonical base 的正式 issue 继续拒绝覆盖 |

步骤 1 同时完成新的 Stage/Branch 数据规则、P036 设计引用 CAS 和最小历史 UI；不能先显示“提交成功”再补原子提交。fork 的通用规则可在此准备，但完整历史分支 UI 和迁移在步骤 4 验收。步骤 2 的界面折叠可与已有后端改造并行，不依赖整体开发完毕。

测试沿用现有模块的行为测试和 UI 测试，新增只覆盖上述真实缺口：接受原子性、断线/迟到结果、图纸精确来源、增量执行和旧数据读回。代码改动运行相应检查与 `python tools/archcheck.py`；API 契约变动同步 OpenAPI 生成客户端；本方案文档只查链接和 scoped diff。

现有 P111 的候选续改能力继续使用，不重开已完成迁移。实施登记沿 [P115](mapping/planning/P115-capability-consolidation.md)、[P108](mapping/planning/P108-vibe-modeling-frontend.md) 和 [P111](mapping/planning/P111-continuing-design-cycle.md) 的现有归属收拢，不为四个名词新增四张平台建设卡。2026-09-24 起，后续工作按 #60 以 GitHub Issue 登记，不再回填这些卡。

## 11. 本次核对的主要代码依据

- [portfolio](../archflow/state/design_portfolio.py)：现有 lineage owner 及旧 SchematicOption/run 绑定限制；[StateRecord](../archflow/state/state_record.py)：operator、依赖和仍被 compiler 使用的 developed-design 投影。
- [WorkingCopy / episodes](../apps/archflow-studio/api/archflow_studio_api/application/episodes.py)：common base、scope、A/B 和保存选择；[candidate](../apps/archflow-studio/api/archflow_studio_api/application/candidate.py)：先完整 native 执行、后 composed 合成。
- [jobs](../apps/archflow-studio/api/archflow_studio_api/application/jobs.py)、[proposals](../apps/archflow-studio/api/archflow_studio_api/application/proposals.py)、[runner](../monkeyarch/runtime/project_runner.py)、[cad_patch](../archflow/adapters/cad_patch.py)：并发冲突、复用与增量执行边界。
- [project ports](../archflow/project/ports.py)、[repository](../archflow/project/repository.py)、[layout](../archflow/project/layout.py)、[issue](../archflow/project/issue.py)：现有持久化范围、原子发布和本方案需要补充的设计 branch 引用。
- [App](../apps/monkeyhub/web/workspaces/src/app/App.tsx)、[VersionsStrip](../apps/monkeyhub/web/workspaces/src/features/stage/VersionsStrip.tsx)、[Stage](../apps/monkeyhub/web/workspaces/src/features/stage/Stage.tsx)、[DocumentCanvas](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DocumentCanvas.tsx)：当前视图、来源和显示生命周期。
- [drawing_elevation](../monkeydiagram/drawing_elevation.py)、[既有出图方案](DRAWING_MODULE_ARCHITECTURE_PLAN.md)、[SYSTEM_MAP](SYSTEM_MAP.md)：已实现立面消费者和其他能力边界。

## 12. 本地实施结果（2026-09-09）

- **完成回收与预览：** 常驻 App 读取候选任务和产物，对话卡片只展示状态；验证与模型打开独立。下载、解析、视图来源变化均有对应处理。真实 App 与 3DM 解析已覆盖对话关闭、验证慢／失败、首次产物读取失败、迟到并行结果、损坏文件及切换基底。
- **Stage 与 Branch：** P036 设计引用 CAS、不可变 Stage、显式 S0、接受、历史查看及从旧 Stage fork 已接通。普通 A/B 不产生 Branch；接受复用已生成模型。两轮未接受续改和独立候选合并可在重启后重放；旧正式 base 可继续探索，正式 HEAD 保持独立。默认和历史 Stage 均固定其确切模型，续改合成也使用该 Stage 固定的源运行记录。
- **图纸：** Studio 已接四个轴向立面，复用现有 STEP→SVG／PNG owner；每份图纸保留确切模型、Stage、视图、revision 与一次写入的生成时间；默认只在当前完整模型来源内选择最新生成修订，无匹配时保持空态。模型版本切换和图纸批注保存共用当前上下文，出图直接打开响应的图纸。批注、设计意见和后续候选沿同一图纸修订继续，相同图片内容的其他修订不替换其来源。
- **增量执行：** runner 在执行前读取确切源 run，复用未变 seat 和同 seat 未变元素；CAD 可保留未变最终形体，并补算缺失中间输入。实际测试覆盖共享中间形体、支承 datum 传播、篡改 STEP 拒绝复用、旧记录回退全量，以及复用形体仍参与当前实体相交检查。
- **并行与合并：** 队列允许隔离候选共享读写范围，保留容量和 Rhino 资源互斥。合并使用 StateRecord owner，将支持的独立构件修改归一为一个候选；冲突检查使用基底与全部候选的依赖并集，拒绝同对象写入、共同重建对象及新增依赖引起的读写冲突。
- **旧数据：** 旧 portfolio 写入生命周期退役，必要类型与 reader 保留。WorkingCopy 保留原比较与选择，新记录可保存确切 baseStageRef；旧模型与旧选择不会自动成为已接受 Stage。

本轮立面需要完整且匹配的 OCCT STEP。多 seat 的单份 native 预览不能初始化为完整 Stage；显式完整 composed 模型可建立 Stage，但只有局部 STEP 时仍不能生成全模型立面。平面、剖面、完整施工图和通用 branch merge 继续沿原计划推进。

验证使用临时 P036 项目、真实 OCCT 生产与读回、既有 API 测试和隔离浏览器。用户当前项目的建筑内容与现场编辑效果尚未替用户验收；当前运行服务没有重启，真实项目没有自动迁移。

最终检查：41 个隔离浏览器场景通过（候选／Stage／图纸 23、来源续改 8、文档模型来源 10）；Web 单元基线 76 通过、2 项既有文件条件跳过，收尾修改的 30 项定向单测通过；类型检查与构建通过。对应 StateRecord、P036、runner／OCCT、Stage／drawing、候选及来源验证 API 回归通过；OpenAPI 生成一致性、234 个文件的 archcheck、修改范围 diff 与文档链接检查通过。
