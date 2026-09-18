# MonkeyArch：2026 AI-native 专业 3D / 建筑设计工作台研究

**核验日期：** 2026-09-04
**结论性质：** 产品与界面方案；用户已授权第一阶段 UI/UX 实现，不授权 canonical write 变更。

**基线说明：** 本文最初检查的 checkout 为 `0252b29`。主仓库随后封存 `1e4d9ca`（P109 typed StateRecord operator 与 Studio candidate 收敛）；UI 实现分支 `codex/monkeyarch-ui-ux` 已按父任务要求直接建立在该提交上，没有带入其他工作树内容。

## 1. 结论

MonkeyArch 不应继续以“左侧聊天 + 右侧模型 + 底部版本条 + 多个常驻面板”作为默认产品形态。当前这套壳已经证明了 proposal、candidate、validation、3DM 预览等能力，但它把能力清单同时摊在屏幕上，尚未兑现产品目标：**默认看见的不是一个 UI，而是一栋建筑。**

建议采用一个 **viewport-first、selection-scoped、proposal-mediated** 的工作台：

1. 3DM viewport 占据默认画面；项目、HEAD、当前视图状态只作为低对比状态线存在。
2. 用户选中真实构件后，才出现该构件的 Inspector、可编辑控制和相关命令。
3. 自然语言不是独立聊天目的地，而是当前 selection 的一个操作入口。
4. Agent 只能产生可编辑的 exact-base proposal；近似 ghost、真实 candidate、validated candidate、published state 必须是四个不同状态。
5. Flow、Inspect、System 是按需打开的三种工作镜头，不是三个新的状态源：它们分别读取现有依赖/阶段/决策记录、构件投影、运行与证据。
6. 第一条纵向切片不做全量壳重构。先证明一个真实构件从选择到 proposal、精确候选、验证、人工 commit/reject，再由更新后的 HEAD 重新投影并更新 viewport。

当前不能把现有 `Apply` 叫作 commit。它只启动 `studio-candidate-harness` 候选运行；该 harness 明确不推进项目 Stage，测试也断言它不修改 HEAD。真正的项目发布动作已经由 `archflow.project.issue.issue_run()`、`PromotionDecision@1`、`prepare_transition()` 和 `compare_and_swap()` 拥有，但 Studio 还没有调用它。

## 2. 事实、推断与建议的边界

### 2.1 当前仓库事实

- UI 实现 checkout 为 `codex/monkeyarch-ui-ux`，基线为主仓库 `1e4d9ca`。
- Studio 已采用 `apps/monkeyhub/web/workspaces`（React 19、three.js、rhino3dm-wasm）与 app-local FastAPI BFF；浏览器 DTO 由 OpenAPI 生成。
- 当前 Web 壳是固定 `400px + 1fr`：左侧常驻 `Conversation`，右侧 `Stage`；Evidence 固定后成为第三列。
- `ThreeDmViewport`、3DM picking、semantic user strings、Z-up、fit/front、ghost、compare、annotations 都是可保留资产。
- API 已有：
  - project/state/component catalog 投影；
  - selection/pick 解析；
  - deterministic/agent intent；
  - exact-base proposal 与 impact closure；
  - detached candidate、队列与 3DM artifact；
  - candidate compare 与 validation verdict；
  - proposal reject/modify 与 `DeliberationEpisode@1`。
- 前端目前没有调用 proposal reject/modify；proposal card 只有 `Apply` 和 `Adjust`。
- Studio 没有 canonical issue/commit route。
- 当前 candidate 通过 `studio-candidate-harness` 运行；它产生 run records，但不会修改项目 HEAD，也不会成为 projection reference。
- 主仓库 `1e4d9ca` 已把单值、massing 与 program edits 收敛为 kernel `StateRecordOperator` / `apply_state_record_operator()`，并删除 Studio 的平行实现。本文后续实现顺序以该基线为准。
- 当前 validation 对 `CanonicalProjectState@1` 仍只能构造 empty-facts `CanonicalState(ref=head)`；P110 已记录这一 kernel 缺口。
- 当前 production spine 的设计源是 `StateRecord@1 + developed_design_view`。`DesignStateTree` 仍有控制/研究用途，但已不是 canonical project-state 的平行来源。

### 2.2 产品推断

- 现有能力不是不足，而是被常驻面板同时暴露。把面板重新命名或换色不会得到 AI-native 工作台。
- 用户真正需要连续看见的是：**建筑、当前选中对象、候选差异、能否接受，以及接受后项目处于哪里。** receipt、event、hash、provider timing 都重要，但只在判断受阻或用户主动 Inspect/System 时重要。
- “Flow”若默认画成全项目节点图，会把 Rive 的图编辑器外观误当成 ArchFlow 的产品价值。对于日常设计，Flow 应只回答当前决定：它改了什么、传播到哪里、哪些检查/Stage 阻止 commit。
- “Agent 编辑真实对象”的关键不是让浏览器执行 CSG，而是 Agent 与人通过同一套 typed proposal 和 kernel execution 作用于同一 `StateRecord` / 真实 `Element@1`。

### 2.3 设计建议

- 默认采用安静、稠密、单一矿物靛蓝焦点的专业编辑器语言；现有偏冷的 Unreal-style 深灰需要改为更中性、略暖的 graphite，去掉把“AI”视觉化成紫色、霓虹或大面积发光的做法。
- 使用 8px 基准网格、1px 低对比分隔、12–14px UI 字号、150–250ms 仅表达层级变化的动效；支持 reduced motion。
- 键盘路径与鼠标路径同等完整：`Ctrl/Cmd K` 打开 Context Actions，`Space` 暂时 Peek，`Esc` 逐层退出 review / inspector / flow。
- 不依赖 hover 暴露唯一动作；选中、验证、阻塞除颜色外必须有文字/图标状态。

## 3. 五个参照产品：可取之处与不可照搬之处

| 参照 | 官方核验事实 | 它解决的问题 | 不可照搬 | 映射到 MonkeyArch |
| --- | --- | --- | --- | --- |
| Linear | 2026 refresh 明确追求在高信息密度下只强调当前任务；弱化侧栏、图标和分隔，让结构“被感到而不是被看到”。Peek 用 Space 临时查看选中项；Display options 按 view 控制可见属性。 | 稠密专业工具如何不抢用户注意力；详情怎样临时出现。 | Linear 是 list/issue-first；常驻导航和 issue 属性模型不适合建筑 viewport。 | 默认弱化 project chrome；Component Tree 变成 Peek；Inspector/Flow/System 按 selection/decision 出现；状态和属性由当前 view 决定。 |
| Framer 3 | Agent 在 Properties panel 中读取 canvas；选中 section 后可把修改限制在该区域，结果直接成为可编辑 canvas 内容。Branch 是整个项目的隔离副本；通过 Review Changes → Apply 回 main，Apply 不等于 publish；重叠改动可能仍需人工选择。 | Agent 产物如何继续手工编辑；AI 变化怎样安全隔离、审阅、应用。 | 不把全建筑复制成新的项目分支；不宣称已有自动 merge engine；不把 Apply 与发布混成一个动作。 | selection chip → exact-base proposal；candidate run 是隔离工作区；compare/review 后由现有 issue gate 更新 HEAD；冲突由 stale-base/closure 明确拒绝。 |
| Spline V2 | Agent 通过与人相同的 editor commands 读写 objects/materials/lights/cameras/interactivity；每次 Agent edit 是正常 editor edit，进入 undo/version history、同步协作，并保留发送时的 selection chips。V2 还提供 Edit/Code/Preview 与 MCP。 | Agent 不产生一次性结果，而是操作真实对象；上下文和编辑历史保持可见。 | Spline 是通用实时 3D 场景工具；其 code mode、坐标自由度和 WebGPU 架构不能替代建筑语义、Stage、验证与发布权限。 | 同一 `Element@1`/Component catalog 供人和 Agent 选择；同一 proposal/runner 实现编辑；selection 与 proposal 永久绑定；viewport 只呈现 kernel 产生、receipt 认证的 artifact。 |
| Rive | State Machine 由 Graph、States、Transitions、Layers 组成；现代 Rive 对新项目推荐 Data Binding/View Models，旧 Inputs 与 Events 是迁移对象。 | 复杂交互状态如何用少量明确状态与条件表达。 | 不复制动画状态机当建筑状态；不采用已弃用 Inputs；不把完整 DesignState 画成常驻 spaghetti graph。 | 只借用有限状态表达：Current → Proposal → Candidate → Validated → Issued/Rejected；Flow 由现有 edges、closure、Stage 与 episodes 派生。 |
| Raycast | Root Search 提供统一入口；选中任何 item 后，Action Panel 才显示上下文动作，Enter 执行 primary action，`Cmd/Ctrl K` 展开完整动作。 | 大量能力怎样按意图渐进显露；专家如何用键盘快速到达动作。 | 命令面板不能取代 viewport picking、直接操控和连续空间判断。 | `Ctrl/Cmd K` 读取当前 selection/view/proposal，给出可执行动作；默认不显示长工具栏；最常用 primary action 在当前状态原位出现。 |

### 官方来源

- Linear：[A calmer interface for a product in motion](https://linear.app/now/behind-the-latest-design-refresh)、[Peek preview](https://linear.app/docs/peek)、[Display options](https://linear.app/docs/display-options)
- Framer：[Framer 3.0](https://www.framer.com/updates/framer-3)、[How to use Agents](https://www.framer.com/help/articles/how-to-use-agents/)、[How to use branches](https://www.framer.com/help/articles/how-to-use-branches-in-framer/)
- Spline：[Introducing Spline V2](https://blog.spline.design/spline-v2)、[AI Agent](https://docs.spline.design/spline-ai/ai-agent)、[AI 3D Generation](https://docs.spline.design/generate/ai-3d-generation)
- Rive：[State Machine Overview](https://rive.app/docs/editor/state-machine/state-machine)、[Data Binding Overview](https://rive.app/docs/editor/data-binding/overview)、[Listeners](https://rive.app/docs/editor/state-machine/listeners)
- Raycast：[Action Panel](https://manual.raycast.com/action-panel)、[Quicklinks / Root Search](https://www.raycast.com/core-features/quicklinks)

## 4. 参考机制到 ArchFlow 现有对象的映射

| 产品概念 | ArchFlow 现有机制 | UI 表达 | 边界 |
| --- | --- | --- | --- |
| 真实可编辑对象 | `StateRecord@1` 的 `Element@1`、`Component@1`；3DM object 的 `archflow:*` strings；Studio catalog/pick resolver | viewport selection + selection chip + Inspector | 浏览器不根据相似名称猜对象，不新建第二套 object id。 |
| Design state | canonical `StateRecord@1 + developed_design_view`，HEAD 由 P036 指向 authoritative/derived refs | 顶部只显示 `HEAD vN`；详细 ref 在 System | 不把 `DesignStateTree` 作为另一个持久状态源。 |
| Stage | `ProjectStageWorkflow`、`StageRunEnvelope`、closure、exit binding | review rail 的 Stage gate；Flow 中显示当前位置与阻塞 | `studio-candidate-harness` 不能冒充项目 Stage。 |
| Agent edit | `POST /api/intents` → typed proposal；deterministic grammar 负责已知直接修改 | 自然语言栏 + selection chips；Agent 解释与 record answer 分层 | Agent 没有 canonical write；browser 不执行几何。 |
| Proposal | Studio `Proposal` + exact base + protected refs + closure；下游 `GeometryProgramProposal`/runner | ghost + Change / Affected / Keep；值可手工 refine | ghost 是近似显示，不是 artifact，也不进 receipt。 |
| Branch / candidate | P036 named run + speculative workspace + certified artifacts | Candidate 状态、before/after、版本 peek | 不新增 client branch store；不把 candidate 与 HEAD 比较为同一 identity。 |
| Commit | `archflow.project.issue.issue_run()` → `PromotionDecision` → `prepare_transition` → P036 CAS | 仅在 real-stage candidate validation 通过后出现 `Commit to HEAD` | 当前 Studio 没有该路径；不得直接让 harness 调 `issue_run`。 |
| Reject / modify | `POST /api/proposals/{id}/decision` + `DeliberationEpisode@1` | Proposal/Candidate review 内 `Reject`、`Revise` | 当前 Web 尚未接线；built candidate 的拒绝应留在该 run，不应只留进程内。 |
| Component Tree | state projection 的 component hierarchy + elements + catalog capabilities | Space Peek / `Ctrl K → Find component` | 保留投影逻辑，退役常驻 picker 形态。 |
| Inspector | `CapabilityPanel`、picked numeric fields、capability source/status、Frame/Program/Options | 右侧 selection-scoped Inspector | derived/locked/missing 必须显示来源，不能伪装可编辑。 |
| Flow | dependency edges、`StateRecord.closure`、Stage workflow、validation clauses、episodes | 当前决定的 target → closure → checks → Stage；必要时展开全局 | 只派生显示，不建 Graph store，不在浏览器算 closure。 |
| 3DM viewport | `ThreeDmViewport`、certified artifact bytes、pick/ghost/compare | 所有状态的视觉主场 | local file 继续明确 `LOCAL/unbound`；只有 receipt-bound export 可成为 candidate/current。 |

## 5. 五个关键界面状态

以下线框描述布局和交互，不是视觉稿。四周留白表示被隐藏的能力，而不是待补的面板。

### 状态 A：默认工作台 — Building first

```text
┌ MonkeyArch   Villa Rotonda · HEAD 7                         ⌘K   ● Ready ┐
│                                                                          │
│                                                                          │
│                             [ 3DM BUILDING ]                              │
│                                                                          │
│                           orbit / pan / zoom                              │
│                                                                          │
│ CURRENT · published                                    Fit  Front  Views │
│                                                                          │
│                 Ask about or change this building…              [↑]      │
└───────────────────────────────  Design  ──────────────────────────────────┘
```

交互：

- 默认没有 Conversation、Component Tree、Inspector、VersionsStrip、Evidence Drawer。
- 顶栏只保留项目名、HEAD、连接/运行状态和 Context Actions。
- `Ctrl/Cmd K` 打开当前情境命令；空 selection 时只给 Find component、Open local 3DM、Views、System。
- prompt 可接受全局问题；涉及修改时，系统必须先得到真实 selection 或提出一个具体问题。

### 状态 B：选中构件 — Selection reveals control

```text
┌ Villa Rotonda · HEAD 7                                        ⌘K   ● Ready ┐
│                                                   ┌ Inspect ──────────────┐ │
│           [building; west portico highlighted]    │ WEST PORTICO          │ │
│                                                   │ portico-base          │ │
│                                                   │ Height 1.873  editable│ │
│                                                   │ Base   level-01 derived│ │
│                                                   │ Span   3.20    editable│ │
│                                                   │ ───────────────────── │ │
│ [WEST PORTICO / portico-base]                     │ 2 downstream elements│ │
│                                                   └───────────────────────┘ │
│ [portico-base]  Make it taller…                                  [↑]        │
└ Space: component peek · Esc: clear selection ───────────────────────────────┘
```

交互：

- viewport pick 仍经 `/api/pick/resolve`；只有 `resolved` 才建立编辑上下文。
- 右侧 Inspector 只展示被选构件的 authored、derived、locked、missing controls；derived 值显示来源。
- `Space` 临时打开 Component Tree Peek，并随上下键切换选择；放开 Space 即关闭。
- 选择进入 prompt chip；历史名称变化不改变该 proposal 已绑定的 element/component id。

### 状态 C：自然语言修改 — Agent is a tool of the selection

```text
┌ Villa Rotonda · HEAD 7                                         Agent reading ┐
│                                                    ┌ Intent ───────────────┐ │
│              [selected component stays visible]    │ Target  portico-base  │ │
│                                                    │ View    front-west    │ │
│                                                    │ Marks   ↑ +Z          │ │
│                                                    │ Keep    pediment-west │ │
│                                                    └───────────────────────┘ │
│                                                                              │
│ [portico-base] [↑ mark] [keep pediment]                                     │
│ Make this a little taller, but keep the pediment line.            [Stop]     │
└──────────────────────────────────────────────────────────────────────────────┘
```

交互：

- prompt 上方显示 selection、gesture、viewpoint chips；它们是发送内容的一部分，不是装饰。
- Agent 只把自然语言编译成已有 grammar/typed proposal；如果缺数值或真实 control，原位提问。
- 已知 control 的 slider/step 修改走 deterministic grammar，不重复调用 Agent。
- Agent 的 `why` 与 record/kernel 返回的 target/change/closure 分开显示。

### 状态 D：Proposal / Candidate 审阅 — Difference before decision

```text
┌ Villa Rotonda · HEAD 7                          GHOST PREVIEW · approximate ┐
│                                            ┌ Proposed change ─────────────┐ │
│      [current building + blue ghost]        │ portico-base.height          │ │
│                                             │ 1.873  →  2.200              │ │
│        [affected elements faint]            │ Affects 2 · Unknown 0        │ │
│                                             │ Keep pediment-west           │ │
│                                             │ [ slider / − / + ]           │ │
│                                             │                              │ │
│ ◀ Before ─────────────── After ▶            │ [Build exact candidate]      │ │
│                                             │ [Reject] [Revise]            │ │
│                                             └──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘

候选完成后，同一 rail 原位变为：

```text
Candidate exact · validation passed · Stage DD gate satisfied
Changed 1 · affected 2 · unchanged 27     [Commit to HEAD] [Reject] [Revise]
```

交互：

- Proposal 到达即显示 approximate ghost；`Build exact candidate` 才启动 runner。
- candidate artifact 到达后自动切为 before/after compare；不把 ghost 当 exact geometry。
- `Commit to HEAD` 只有在 real-stage run、exact-base、validation 与 Stage gate 全部通过时出现。
- Reject 关闭当前 proposal/candidate，HEAD 不变；Revise 回到同一 selection 的 composer。
- Commit 成功后重新拉取 `/api/project`、`/api/state` 和 artifacts，显示 `HEAD 8`，并把新 issued artifact 作为 Current。

### 状态 E：Flow / Inspect / System — 三种按需镜头

```text
FLOW                                  INSPECT                     SYSTEM
┌ current decision ───────────────┐   ┌ selected object ───────┐  ┌ project/runtime ──────┐
│ portico-base.height             │   │ authored controls      │  │ HEAD / project refs   │
│      ↓ support / dependency     │   │ derived sources        │  │ jobs / queue          │
│ column-stack-west               │   │ object bindings        │  │ receipts / events     │
│      ↓ invalidates              │   │ geometry/material      │  │ provider / timings    │
│ portico-roof-west               │   │ validation findings    │  │ protocol / errors     │
│      ↓ Stage checks             │   └────────────────────────┘  └────────────────────────┘
│ [2 held] [0 violated] [0 unchecked]│
└─────────────────────────────────┘
```

交互：

- Flow 默认只显示当前 proposal 的 target、closure、protected refs、validation clauses 与当前 Stage；“Show all” 才展开全项目。
- Inspect 是状态 B 的持久工作镜头，可在 viewport 与 component tree 中同步选择。
- System 接收当前 Evidence Drawer、EventStream、receipts、honesty、timings、server identity；默认隐藏，阻塞时由 inline status 提供 `Open System`。
- 三个镜头都读取服务端投影，不持久化自己的状态，也不决定 verdict。

## 6. 现有组件处置

| 现有组件/机制 | 处置 | 新位置或作用 |
| --- | --- | --- |
| `ThreeDmViewport.tsx`, `sceneInspection.ts` | 保留 | 默认工作台主体；继续 load/pick/ghost/compare/annotation。 |
| `AppShell.tsx` | 改造 | 从固定 `400px + 1fr` 改为 viewport + transient rail/dock。 |
| `Conversation.tsx` | 隐藏并拆出决策线程 | 不常驻；prompt、问题、proposal/review 在 selection 周围原位出现；完整 transcript 从 System/History 打开。 |
| `Composer.tsx` | 保留逻辑、改造形态 | 底部 command composer；selection/gesture/viewpoint chips 常显。 |
| `ComponentTree.tsx` | 保留投影与选择逻辑、改为 Peek | Space / command palette / Inspect 内打开，不常驻。 |
| `CapabilityPanel.tsx` | 改造为 Inspector 主体 | 只显示当前 selection 的真实 control 与来源。 |
| `ProposalCard.tsx` | 改造为 Review Rail | 保留 Change/Affected/Keep/refine；`Apply` 改成 `Build exact candidate`；接入 Reject/Revise。 |
| `CandidateCard.tsx`, `VerdictCard.tsx`, `CompareCard.tsx` | 合并呈现，不合并领域值 | 同一 review rail 的 Candidate → Validation → Decision 三段。 |
| `Stage.tsx` | 瘦身 | viewport host + 最小状态 HUD；Frame/Options/Program 从常驻 toolbar 移到 context actions/Inspect。 |
| `SourceChip.tsx` | 保留、降噪 | Current / Ghost / Candidate / Validated / Published 五种清晰状态。 |
| `VersionsStrip.tsx` | 默认隐藏 | Proposal review 或 `Version Peek` 时出现；不再占据每个默认画面。 |
| `FrameEditor`, `OptionsPanel`, `ProgramPanel` | 保留 | 作为 Inspect 的 context-specific panes，而非三枚常驻 viewtools。 |
| `EvidenceDrawer`, `HonestyTab`, `ReceiptsTab`, `EventStream` | 移入 System | 原数据和 server wording 保留；不在默认设计面持续竞争注意力。 |
| `SettingsPanel` | 保留 | 只从 Context Actions / System 进入。 |
| 当前永久 400px chat column | 退役 | 没有新的平行会话/Store；其 transcript 仍由现有 `useTranscript` 承载。 |
| 浏览器内新增 Branch/Graph/Receipt Store | 不做 | branch/run、closure、receipt 继续由现有 P036/kernel/API 拥有。 |

## 7. 最小纵向切片：一个构件，一次真实状态推进

### 7.1 目标

在一个配置好的 P036 测试项目上证明：

```text
certified 3DM object
  → resolved Element@1 / Component@1
  → agent-compiled, editable exact-base proposal
  → approximate ghost
  → exact candidate on the project's real Stage
  → before/after + kernel validation
  → human commit or reject
  → durable decision + HEAD/projection/viewer update
```

### 7.2 必须先闭合的现有缺口

1. **采用主仓库已落地的 P109。** `1e4d9ca` 已提供 typed `StateRecordOperator` → successor `StateRecord@1` 并删除 Studio 平行 successor；纵切直接复用该 kernel 入口，不再另造 edit path。
2. **P110 仍需先落地。** `CanonicalProjectState@1` + bound StateRecord 能投影成 validator 真正读取的 `CanonicalState`。否则 “validation passed” 主要只证明 artifact 存在。
3. **candidate 不再使用 harness 作为可发布运行。** 纵切必须读取项目现有 `ProjectStageWorkflow`，打开真实下一 Stage 的 retained envelope/guard，得到该 Stage 的 closure 与 exit binding。harness 仍可服务快速试算，但永远不显示 Commit。

### 7.3 最小实现边界

- 扩展现有 Studio BFF，不新增顶层 client/server/shared。
- 新增的 commit adapter 只做一件事：检查 candidate/validation/current HEAD，然后委托现有 `issue_run()`；canonical transition 仍由 `archflow.project.issue` 与 P036 repository 拥有。
- 把当前 `episodes.accept()` 从“启动 candidate 时”移动到“issue 成功时”；同一改动退役旧语义。运行 candidate 只是产生待审候选。
- Reject 复用现有 proposal decision / `DeliberationEpisode@1`；如果 candidate run 已存在，拒绝记录直接写入该 run，而不是等下一次 run 或只留进程内。
- Web 只增加一条真实产品路径：selection → composer → review rail → build candidate → compare/validate → commit/reject → reload projection。
- 不增加 Store、Receipt、branch model、fallback、兼容 DTO 或第二套 state graph。

### 7.4 验收条件

#### 选择与 proposal

- 从 receipt-bound 3DM 点击对象，服务端返回唯一 `componentId + elementId`；local/unbound/ambiguous 均不能进入 commit 路径。
- proposal 绑定当前 project、HEAD version/state SHA、StateRecord digest 与 target；stale base 明确拒绝。
- Agent 输出必须通过现有 deterministic grammar/record check；slider refinement 不再次调用 Agent。

#### 预览与候选

- ghost 在 300ms 级交互内出现并标记 `approximate`，不进入 artifacts/receipts。
- exact candidate 使用 P109 successor record 和真实 Stage guard；3DM export 继续由 runner/adapters 产生并 readback verify。
- before/after 列出 changed / affected / unchanged；浏览器不自己计算 closure 或 geometry diff。

#### 验证与 commit

- P110 使 validation 实际读取 canonical obligations/commitments/facts；至少一个测试能触达 obligation 或 authorized-commitment finding。
- Commit button 只有在 candidate exact-base、seat complete、relations held 且 fully checked、exports certified、Stage closure satisfied 时可用。
- Commit 调用现有 `issue_run()`；成功后 HEAD 从 `vN` 变为 `vN+1`，canonical authoritative ref 指向该 run 的 StateRecord，projection 中目标值变为 proposal 新值。
- 提交成功后 UI 重新读取 project/state/artifacts；新 issued model 成为 `CURRENT · published`，旧 proposal/candidate rail 关闭但 decision history 可查。
- 并发改变 HEAD 时，existing stale/CAS path 拒绝 commit；UI 回到 compare/rebase，而不是自动重放。

#### Reject

- Reject 后 HEAD version、state SHA、authored record 都不变。
- proposal/candidate 状态变为 rejected；reason、validation refs、candidate run 被同一个 `DeliberationEpisode@1` 持久保留在该 run。
- 同一 rejected proposal 不再显示 Commit；用户可从相同 selection 发起新 proposal。

#### 可用性

- 默认 1440px 画面 viewport 占工作区至少 80%；没有永久 chat、tree、evidence 或 versions column。
- 所有核心动作可用鼠标和键盘完成；focus visible；状态不只靠颜色；reduced motion 下层级仍清楚。
- async 超过 300ms 显示 inline status；候选的 reasoning、kernel、export 阶段使用已有 timing/queue 数据，不伪造百分比。

## 8. 实施顺序

1. **同步新基线并补 Kernel prerequisite：** 采用已落地的 P109，只继续 P110；保留现有 DTO 形状，不恢复被删除的 Studio candidate implementation。
2. **真实纵切：** 一个 real-stage project fixture + issue adapter + commit/reject API 行为；先用 API 测试证明 HEAD 与 durable episode。
3. **单一 Review Rail：** 在现有布局中接通选择、proposal、candidate、validation、commit/reject；此时不要先改全壳。
4. **Viewport-first shell：** 纵切稳定后再退役 permanent conversation column，把 Tree/Evidence/Versions 迁到 Peek/Inspect/System。
5. **Flow lens：** 最后基于已有 edges/closure/stage/episodes 增加派生视图；没有真实消费者的全局 graph 不做。

## 9. 明确不做

- 不创建第二套 client/server/shared、前端 domain models、branch store 或 canonical state。
- 不让 Agent、browser、3DM viewer、BFF 自行 commit。
- 不把 harness candidate、ghost、local 3DM、unchecked relation 或 empty-facts validation 画成“可提交”。
- 不先做全局 Flow graph、多人 merge、code mode、WebGPU 迁移或全量工具栏重构。
- 不因参照产品具备某项能力，就在 MonkeyArch 增加没有现有记录、API 或验收消费者的抽象。

## 10. 对 UI/UX 规则库建议的取舍

本次使用的本地 UI/UX 规则库对“专业编辑器”匹配出的有效部分是：Developer Tool / IDE 的 dark minimal、Swiss rational grid、单一 accent、progressive disclosure、明确 loading/focus/reduced-motion。其第一次综合检索给出的 “AI purple + pink、夸张极简、营销漏斗”与 MonkeyArch 的专业 3D 工作台不匹配，未采用。这里的产品结构以仓库机制和官方交互证据为主，不以通用视觉模板替代判断。

## 11. 视觉参照的真正启发

这里区分产品公开说明与对 MonkeyArch 的设计推断；不把参照产品的外观当作可直接复制的组件库。

| 参照 | 官方可核验的视觉/层级原则 | 对 MonkeyArch 的有效启发 | 不采用的表面特征 |
| --- | --- | --- | --- |
| Linear 2026 | 高密度界面中，主任务保持清晰，导航退后；减少图标和无必要分隔。默认色从偏冷蓝灰移向更暖、低饱和但仍清晰的灰。 | 让 viewport 和当前 decision 获得最高明度差；侧栏、Tree、System 不应和模型争夺注意力。中性底色可以略暖，避免工具看起来像游戏引擎。 | 不复制 issue-list 密度，也不把所有层级压成同一种灰。 |
| Framer 3 | Agent 进入 canvas 工作流，Branching 把试验和主项目分开。 | Agent 使用与属性编辑同一套选择、审阅和应用语言；视觉上不需要一个独立“AI 世界”。 | 不使用大面积品牌渐变或网站编辑器的营销感。 |
| Spline V2 | 3D editor 以 canvas 为中心，Agent 在 sidebar 中使用同一编辑系统；Edit / Code / Preview 按模式切换。 | 模型是内容层，工具是按任务出现的功能层；Agent 状态靠 selection chip、运行状态和真实 scene change 表达。 | 不引入 Code mode，也不为了“未来感”加入霓虹、发光网格或彩色材质 chrome。 |
| Raycast 2 | 新界面在 macOS Tahoe 中克制使用 Liquid Glass，同时保留专业感；官方技术说明强调原生行为、透明内层和平台约定，而非单纯换皮。 | 命令面板可成为最明显的浮层；跨平台应用应追随各平台行为，并保持共同信息架构。 | 不把 macOS hover、窗口材质和按钮行为一比一搬到 Windows WebView。 |
| Apple macOS / HIG | Liquid Glass 是浮在内容之上的 controls/navigation 功能层；内容层使用 standard materials。颜色应语义化、动态适配，并只给主要动作或状态强调。sidebar、canvas、inspector 是成立的 split-view 组合，但 sidebar 应可隐藏，selection 跨 pane 保持连续。 | viewport 保持哑光；顶部工具、命令面板、短时 HUD 可轻微玻璃化。Inspector 用更不透明的 standard material 保证数值和证据可读。 | 不做“整个应用都是玻璃”；不在明暗复杂的 3D 画面上直接放低对比文字；不让每个按钮都染色。 |

设计依据：[Linear 2026 visual refresh](https://linear.app/now/behind-the-latest-design-refresh)、[Framer 3](https://www.framer.com/updates/framer-3)、[Spline V2](https://blog.spline.design/spline-v2)、[The New Raycast](https://www.raycast.com/blog/the-new-raycast)、[Raycast technical deep dive](https://www.raycast.com/blog/a-technical-deep-dive-into-the-new-raycast)、Apple [Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Color](https://developer.apple.com/design/human-interface-guidelines/color)、[AppKit new design](https://developer.apple.com/videos/play/wwdc2025/310/)、[Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars)、[Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars)、[Split views](https://developer.apple.com/design/human-interface-guidelines/split-views)。

## 12. 推荐方向：Workshop Graphite + Drafting Blue

### 12.1 一句话

**像建筑制图台，而不是 AI dashboard：暖石墨工作面承载模型，低饱和制图蓝只标记“当前选择、键盘焦点或唯一主操作”，验证状态使用独立材料色；Agent 本身不拥有颜色、头像或动画。**

这一轮把第一阶段偏饱和的 `#7388F2` 再收敛为 `#7FA6D2 / #356B9E`，同时取消常驻毛玻璃和选中物体的强 emissive。目的不是“换一种蓝”，而是让 chrome、模型语义色、selection 和 validation 各自只承担一种含义。

### 12.2 三层表面，不复制 Liquid Glass

```text
L2  Transient functional layer
    command palette / modal / popover
    matte raised surface, only this layer may cast a restrained shadow

L1  Standard work surfaces
    toolbar / Inspector / Review Rail / Flow / System / HUD
    flat graphite or vellum, hard edge, no backdrop blur

L0  Content layer
    3DM viewport / drawing / compare
    matte, color-managed, no blur; model and annotations own the attention
```

- L0、L1 均不使用 `backdrop-filter`；结构依靠明度、间距和 1px 分隔，不靠模糊与阴影墙。
- L2 在复杂模型背景上使用不透明 backing；Windows/Web 不模拟 macOS 的折射、vibrancy 或高光追踪。
- Inspector、Evidence、Version Card 即使覆盖画布也仍是 standard surface；它们承载数值与判断，不是装饰层。
- `current / candidate / blocked` 必须由文字、线型和语义色共同表达，绝不由玻璃材质表达。

## 13. 可落地的颜色系统

### 13.1 核心 token

| 语义 | Dark | Light | 用途 |
| --- | --- | --- | --- |
| `ground` | `#191B19` | `#ECEDE8` | 应用外围主底色；略暖、中性。 |
| `viewport` | `#141614` | `#E3E5E0` | 3DM 空场；比 chrome 更安静。 |
| `panel` | `#1F221F` | `#F4F5F0` | toolbar、Inspector、Review Rail、常规面板。 |
| `raised-surface` | `#272B27` | `#FBFCF8` | 输入、嵌套区和短时浮层。 |
| `hover` | `#303530` | `#FFFFFF` | 只在可交互项 hover / pressed 时出现。 |
| `overlay` | `rgba(31,34,31,.96)` | `rgba(244,245,240,.97)` | 复杂模型上方的硬边 matte backing。 |
| `border-soft` | `rgba(240,241,236,.11)` | `rgba(32,35,31,.13)` | 必需分隔；不依赖边框建立全部结构。 |
| `text-primary` | `#F0F1EC` | `#20231F` | 正文、值、当前状态。 |
| `text-secondary` | `#B3B8B1` | `#59615A` | 标签和辅助说明。 |
| `text-tertiary` | `#8D938C` | `#687069` | 非关键元数据，仍满足小字最低对比。 |
| `accent` | `#7FA6D2` | `#356B9E` | selection、focus ring、唯一 primary action。 |
| `on-accent` | `#0E1B28` | `#FFFFFF` | 强调按钮文字/图标。 |

实测最不利组合：Dark tertiary / raised-surface `4.58:1`，Dark violated / raised-surface `4.67:1`，Dark on-accent `6.87:1`；Light tertiary / raised-surface `4.96:1`，Light accent / raised-surface `5.44:1`，Light on-accent `5.60:1`。这消除了第一阶段 faint / accent / status 小字落到 4.5:1 以下的问题；viewport 上的实际叠层仍需逐态检查。

### 13.2 状态色

| 语义 | Dark | Light | UI 语言 |
| --- | --- | --- | --- |
| `held / ready` | `#75A986` | `#3D7754` | muted jade；勾选图标 + `Held` / `Ready`。 |
| `unchecked / caution` | `#C49A5A` | `#8A6123` | oxide amber；空心状态图标 + 原因。 |
| `violated / blocked` | `#D27B72` | `#A64E47` | clay red；阻塞图标 + clause，不只红边。 |
| `queued / running` | `#A2ADB3` | `#56656E` | neutral steel；只表示普通后台任务，不借用 warning 或 Agent 专色。 |
| `candidate ghost` | `accent` 虚线 + 正常文字 | `accent` 虚线 + 正常文字 | 不在 accent-soft 上叠 accent 小字，也不冒充 published。 |
| `published/current` | 中性文字 + 实心圆点 | 中性文字 + 实心圆点 | 不使用绿色；published 是身份，不是“成功消息”。 |

一个 accent 只表达 interaction / selection。绿、黄、红只表达验证语义；紫色不被单独分配给 Agent，避免把同一真实编辑路径分裂成人和 AI 两套视觉系统。

### 13.3 3D viewport 专用色

- minor grid：Dark `#252925` / Light `#D5D8D2`；major grid：Dark `#343934` / Light `#BFC4BD`。
- selection：accent 的定位线或双描边；当前 fallback 只保留 `0.22` 的 emissive lift，不使用 `0.85` 强发光。
- approximate ghost：accent 18% 面 + 65% 轮廓 + `APPROXIMATE` 标签；exact candidate 才能用稳定实线。
- changed / affected / unchanged：changed 用 accent，affected 用 amber，unchanged 用中性灰；不要同时再叠一套彩虹构件类别色。
- 建筑模型自身的材质、分析图或 semantic colors 优先级高于应用 chrome；工具栏应自动提高 backing，而不是修改模型颜色以迁就 UI。

## 14. 排版、图标与几何

### 14.1 字体

不直接要求 Apple 的 SF Pro，也不增加网络字体依赖。使用平台原生优先的跨平台栈：

```css
--font-ui: "Segoe UI Variable Text", Inter, "SF Pro Text", system-ui, sans-serif;
--font-display: "Segoe UI Variable Display", Inter, "SF Pro Display", system-ui, sans-serif;
--font-mono: "Cascadia Mono", "SFMono-Regular", "Roboto Mono", Consolas, monospace;
```

- 常规 UI：13px / 18px；数值、状态和动作使用 500 weight，不用全大写建立层级。
- 面板标题：14px / 20px / 600；项目名最多 15px，不做 dashboard 大标题。
- 技术 id、digest、坐标才使用 mono；构件名称和操作说明继续使用 UI 字体。
- 关键值使用 tabular numerals，避免 Inspector 数值变化时跳动。

### 14.2 圆角和密度

- control 4px；docked panel 6px；modal / floating surface 8px。圆角表达层级，不表达“友好 AI”。
- pill 仅用于 status、selection chip、紧凑的 mode group；普通按钮保持圆角矩形，避免“每个控件都是胶囊”。
- 桌面控件最小可点区域 32px；top functional bar 44px；面板行高 30–32px。
- 默认 Inspector 320–360px，可拖拽且可完全隐藏；viewport 仍是主面积。
- toolbar 最多三个视觉组：leading 项目/HEAD，center 当前 lens 或直接操控，trailing Inspector / Search / primary action。

### 14.3 图标

- 使用统一 16px 单色 outline icon，1.5–1.75px stroke；不混用填充、emoji、彩色方块和不同视觉重量。
- 借鉴 SF Symbols 的一致性和状态切换原则，但 Web/Windows 实现采用仓库可许可的跨平台 icon set 或现有 SVG，不直接复制 Apple 资源。
- 图标只服务高频、可识别动作；低频动作保留文字。Linear 的教训不是“图标越少越好”，而是每一个图标都要赢得视觉重量。

### 14.4 Draft Monkey / 绘图猴

猴子是 MonkeyArch 的梗和识别资产，不应被去除；需要退役的是它作为循环表演的方式。冷启动采用“拱心石吊猴印记”：拱券、蓝色拱心石、单臂悬挂、耳和卷尾被压成无面部的几何线稿，像建筑师留在图纸边缘的制图签名。

- 完整彩色插画只留在应用、快捷方式和托盘图标；Web cold boot 与 Windows launch surface 使用同源的线稿层级。
- 猴身使用 `ink-2`，拱券退到 `faint`，拱心石使用 Drafting Blue；不恢复棕色卡通角色、琥珀 warning 色、锤子、火花或表情。
- 猴子本身静止，真实 activity rail 是唯一持续动效；启动器仍按真实 `1/8…8/8` 填充，Web 未知时长仍不伪造百分比。
- 只在 cold boot 出现。Stage/local 3DM loading 继续使用 compact status readout，不让品牌图形反复遮挡模型。
- SVG 是装饰图形并从无障碍树隐藏；准确状态仍由 `role=status` 与 `aria-live=polite` 的文字承担。Reduced Motion 下 rail 冻结，印记无需额外处理。

这一分级保留了品牌个性，同时遵循 Apple 对 app icon 与界面 glyph 视觉重量分开的做法；线稿的曲线、负空间与对齐借鉴 [Icons](https://developer.apple.com/design/human-interface-guidelines/icons) 和 [SF Symbols](https://developer.apple.com/design/human-interface-guidelines/sf-symbols)，但不复制系统符号。

## 15. 组件级视觉规则

| 组件 | 建议形态 | 禁止 |
| --- | --- | --- |
| Top bar | 44px、最多三组、平接 matte surface；HEAD 与 Ready 是低对比文字状态。 | 一排常驻 viewtools、多个彩色 CTA、把整条 title bar 染成 accent。 |
| Command composer | 闲置时是一条低存在感输入；有 selection 时显示 chip；执行后原位变成简短进度/下一动作。 | 永久聊天 column、彩色 AI 光晕、伪造百分比。 |
| Inspector | selection-scoped、近乎不透明；label/value 两列，source/locked/derived 有明确小状态。 | clear glass 长文本、用 disabled 灰掩盖值来源、所有字段都有边框。 |
| Review Rail | proposal → candidate → validation → decision 一条纵向链；唯一 primary action 按状态更换。 | 同时出现 Apply / Commit / Publish 三个近义按钮。 |
| Component Tree Peek | 低对比列表、选中项与 viewport 持续同步；Space 松开即退。 | 常驻高亮整棵树、每行多枚彩色类型图标。 |
| Flow | 当前 target 和 closure 用 accent；阻塞项用 semantic status；其他关系退后。 | 全项目默认彩虹 graph、动态发光连线。 |
| System | 数据密度高但视觉等级最低；只有阻塞或断连时被主动提升。 | digest、receipt、provider timing 常驻默认画面。 |
| Toast / feedback | 只报告已经完成或明确失败的事件；靠近触发位置，2–4 秒退出。 | 全局右上角堆叠设计过程中的每一步。 |

## 16. 动效与平台适配

- surface 进入：140–180ms ease-out；退出：100–120ms ease-in；selection outline 80–120ms。动效只解释对象从哪里出现、依附哪个 selection。
- 不使用持续漂浮、呼吸光、折射追踪鼠标或 3D parallax。复杂场景下它们会同时损害性能和判断精度。
- 持续循环只允许真实 indeterminate activity rail。启动器知道 8 个步骤，因此显示 determinate rail；API、session、3DM 本地解析不知道百分比，因此只显示准确动作文字与 indeterminate rail。
- `prefers-reduced-motion` 下 rail 变成静态 8px 状态标记；不保留后台 React timer，也不使用 shimmer。
- 原生 macOS wrapper 将来可以调用系统材质、菜单和键位；Windows/Web 保持同一信息架构，但不自行模拟 vibrancy、折射和高光。共同点是语义 token 与交互路径，不是像素级皮肤一致。
- toolbar actions 同时进入 menu / command palette；context menu 只放当前 selection 的少量高频动作。这样在 toolbar 隐藏或紧凑模式下，能力仍可达。

依据：Apple [Loading](https://developer.apple.com/design/human-interface-guidelines/loading)、[Progress indicators](https://developer.apple.com/design/human-interface-guidelines/progress-indicators)、[Motion](https://developer.apple.com/design/human-interface-guidelines/motion)；Linear 对 [ProKit 与 Liquid Glass](https://linear.app/now/linear-liquid-glass) 的比较；Blender [Status Bar](https://docs.blender.org/manual/en/4.3/interface/window_system/status_bar.html)。

## 17. 最小视觉改造顺序

1. **已完成 — token 与表面：** Workshop Graphite / Drafting Blue 深浅模式、可读小字、平接 toolbar/HUD、低阴影浮层。
2. **已完成 — loading：** 退役 raster 吉祥物循环，保留静态 Draft Monkey 制图印记；启动器使用真实 8 步 rail，Web 使用无图片、无 JS timer 的 activity rail；stage 保留模型上下文。
3. **下一步 — 状态语言：** 收敛 `Current / Approximate / Candidate / Validated / Blocked / Published` 的文字、图标、颜色和线型，避免普通 processing 借用 warning。
4. **再纵切 Review Rail：** 只为已定义的 selection → proposal → candidate → decision 路径建立新视觉组件。
5. **最后退役 permanent chrome：** 纵切可用后，把 Conversation / Tree / Evidence / Versions 迁入 Peek、Inspector、Review 和 System。
6. **验收：** 1440px 下 viewport 至少 80%；亮/暗模型背景都能读清 toolbar；键盘路径完整；200% 缩放不截断；increased contrast / reduced motion 可用；状态无需颜色也可辨识。

不建议先做整站换肤。Linear 的公开复盘说明，小步 feature flag、可实时调整 hue/chroma/lightness 的 token 工具，比在静态稿与代码之间反复抄色更有效。MonkeyArch 可以复用这一方法，但它只是开发调试入口，不成为新的产品状态或持久化体系。

## 18. 两轮实现记录

- 第一轮在现有 `styles.css` token 机制内建立 Graphite / Limestone 深浅主题；第二轮收敛为本文件的 Workshop Graphite / Drafting Blue，three.js viewport 继续读取同一组 CSS variables。
- toolbar、viewport HUD、版本卡、evidence 入口已取消持续 blur 与重阴影；modal 等真正浮层仍保留有限 elevation。
- Conversation 可从 toolbar 收起并恢复；收起后 viewport 占据全部主工作区，不新增持久状态或第二套导航。
- Web loading 已删除 emoji、口号、拟人化文案、四帧 PNG 和 125ms React timer；boot 第一帧展示工作区骨架和静态 Draft Monkey 线稿印记，stage 用 compact matte readout 保留模型上下文。
- WinForms launcher 原生绘制同源静态印记，并保留真实 `1/8…8/8` 步骤、错误转红、消息泵和 watchdog；与步骤绑定的 determinate rail 仍是唯一运动。
- 选中对象的 emissive 从 `0.85` 降至 `0.22` 作为过渡；下一轮应以 edge / corner 定位框替代材质发光。
- 已统一 4 / 6 / 8px 圆角、状态 pill、focus ring、输入框与面板层级；完整应用图标与冷启动线稿形成同一品牌的“印章版 / 制图版”两级表达，不改变产品名称或功能边界。
- 已在真实浏览器检查 Dark / Light boot、主工作区和 Settings，并检查页面内 `backdrop-filter = 0`、loading image = 0；WinForms 5.1 的 `5/8` 启动状态也以真实控件渲染核验。
