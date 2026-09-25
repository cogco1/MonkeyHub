# MonkeyHub UI 现状与功能逻辑审查（2026-09-24）

**基线：** `origin/main` `04e719617d6aa20507b5c1846de3059b5c57a451`
**工作项：** GH-234（lane `ui-audit`）；本文是 #234 中“现状 UI / 代码对照矩阵”的交付，不是重设计方案。新的交互方案由 lane `interaction-proposal` 另行给出。
**方法：** 三个只读代理（均为 `claude-opus-5-5`）分别核查 Hub 外壳、项目工作区和“文档语义 vs 实现”。主代理复核了影响编辑来源和权限的关键项。没有运行应用。

**标注说明：**
- **【复核】** 主代理已亲自读过代码并确认成立。
- **【静态】** 代理静态追踪，有文件与行号，未运行确认。
- **【待确认】** 需要运行或临时项目才能证实。

**路径缩写：**

| 缩写 | 路径 |
| --- | --- |
| `WS` | `apps/monkeyhub/web/workspaces/src` |
| `HUB` | `apps/monkeyhub/web/src` |
| `API` | `apps/archflow-studio/api/archflow_studio_api` |
| `HUBAPI` | `apps/monkeyhub/api/monkeyhub_api` |

## 1. 结论

1. **最严重的一类问题：编辑基准会在没人按“从这个版本继续”的情况下改变。** 有三条独立路径会这样：
   - 生成候选（F1）
   - 在查看中的模型上直接建模（F2）
   - 撤销/重做（F3）

   这直接违反 README 的规则：“Continue from this version explicitly makes the displayed run the next edit's starting point. Browsing a model alone does not change it.”
2. **Hub 里有两个聊天。** 用户从界面上无法判断改模型该用哪一个（F6）。Hub 右侧是可持久、带 agent 的 CLI 聊天；MonkeyArch 里另有一个默认关闭、关掉后打不开、只存在内存里的确定性意图“对话”栏。
3. **AGENTS.md 要求区分的四个动作，界面里只剩“接受为 Stage”一个判断动作（F7）。** 四个动作是：生成候选、继续、认可方向、正式发布。“认可”在 UI 中不存在；“拒绝”也不存在；“接受”有四种叫法。
4. **“可审阅 / ✓ held”在零关系时恒为真，而这个就绪状态正是接受 Stage 的门槛（F8）。**
5. **9-04 工作台方案只部分落地（第 6 节）。** 该方案只管 MonkeyArch，而现在的 Hub 已有 Modeling、Render、Drawings、Board、Fabrication、Usage 六个入口，外加进行中的 Publish。缺的是 Hub 级的信息架构。

## 2. Hub 外壳现状（摘要）

### 页面结构
单页，没有路由（`HUB/main.tsx:300-302`）。`ChatShell` 是一个网格：
- **左侧栏**：项目与聊天、归档、Hub 设置。
- **中间**：聊天。
- **可拖宽的右侧工具面板**：默认 620px。
- **76px 右轨**：
  - “Workspaces”组：Modeling、Render、Drawings、Board、Fabrication。
  - “System”组：Usage。

打开过的工具页隐藏后仍保持挂载（`HUB/ChatShell.tsx:37-44, 968-993`）。
- URL 只用 `replaceState` 改写，浏览器“后退”不会在聊天或工具之间回退。
- 视图状态存 localStorage，聊天记录由服务端保存。

### 项目打开
选中项目即自动 `ensureProject`：
1. 打开运行时。
2. 必要时启动 Studio，每 400ms 轮询一次，最长 60s。
3. 校验绑定。

工作区与 Hub 在同一个 React 树内，经 `/api/runtime/projects/{id}/studio` 转发（`ChatShell.tsx:392-456, 974-988`）。

### 聊天
- 新的 Codex 聊天走 ACP，另支持 Claude Code 等 CLI。
- 支持附件、按聊天选模型、权限提示、进度行、“在右侧打开此候选”。
- agent 通过结构化 Studio 调用生成候选（`HUBAPI/chat.py:2808-2839`）。

### 设置
全局只有一个“Hub settings”对话框（`HUB/main.tsx:212-294`），工作区内没有设置入口。

### 视觉
- `apps/shared-web/src/base.css` 提供颜色 token 和四级字号角色。
- 外壳基本遵守；工作区样式自有一套 `calc(Npx*var(--font-scale))` 字号。
- Monitor 使用裸 px。
- 桌面启动页只有暗色。

### i18n
- 一套文件内有三个目录：工作区 790 键、`hubCopy`、`chatCopy`，中英键齐。
- 另有多个局部目录和绕过目录的硬编码字符串。

## 3. 项目工作区现状（摘要）

### 布局
`ProjectWorkspace` 为每个项目挂载 arch、render、drawing、board 四个面。首次访问后只隐藏、不卸载（`WS/app/ProjectWorkspace.tsx:55-69, 123-148`）。

MonkeyArch 的布局：
- 可选的“对话”栏，默认关闭（`WS/app/App.tsx:494`）。
- Stage，自下而上依次是：
  - 视口
  - 覆盖层
  - HUD
  - 四组工具栏：选择与绘制、编辑、视图、同步
  - 底栏：版本/查看中/已选中；“下一次编辑从……开始”与“从这个版本继续”
- 默认 620px 面板下，1440px 窗口里视口约占 43%。9-04 方案的目标是 ≥80%。

### 状态
`App.tsx` 共 3534 行：
- 48 个 `useState`、57 个 `useRef`、37 个 `useEffect`。
- 承担约 21 类职责。
- `Stage` 接收 77 个 props。
- 有若干状态从未被读取：`inspection`、`view`、`persistenceFailed` 等。

### 核心流程

| 流程 | 现状 |
| --- | --- |
| 从 Hub 聊天改模型 | 候选自动在右侧打开，只作查看，不显示提案卡、ghost 或结论卡（`App.tsx:1960-1986`） |
| 从 Arch 对话改模型 | 提案卡 → Apply → 候选卡 → 结论卡 |
| 接受 | 只在 Versions 面板里，需要 `design-history` 能力 |
| 拒绝 | 没有 |
| 正式发布 | UI 中不存在 |
| 直接建模 | 先生成本地草稿命令，Sync 后链式提案 → 候选 → 在用户未再动模型时被静默采纳为新基准 |
| 导入 Rhino | 只读查看 |

### 三个工作区如何共存
- Board 的图页编辑在 arch 面里打开，此时右轨仍高亮 Board。
- 右轨“Drawings”打开的是 `DrawingCanvas`（剖切出图）；“在 MonkeyDiagram 中打开”打开的是 `DocumentCanvas`（图页编辑）。两个编辑器共用“MonkeyDiagram”一名（`App.tsx:3197-3200`）。

## 4. 功能逻辑矛盾（按对建筑师的误导程度排序）

### F1 生成的候选在冷启动后成为编辑基准【复核】
- **应有语义：**
  - 生成候选、继续、认可、发布是四个不同的动作（`AGENTS.md`）。
  - “Opening a candidate is looking at it”（`apps/monkeyhub/README.md:34`）。
- **实际：**
  - 每个候选运行（agent 提案、选项、任务书、合并都算）都会调用 `record_candidate_draft`（`API/application/candidate.py:390-391`）。
  - 只要工作草稿位置为空，或者正是该候选的来源，就把它移到新候选上（`API/application/working_draft.py:105-122`）。
  - 冷启动时，编辑基准从这个位置恢复：`savedChoice = workingDraft.current`（`WS/app/useSession.ts:97-101, 113-114`）。冷启动包括刷新、Hub 重启，以及 Hub 为提供聊天上下文而挂载的隐藏工作区。
- **补充：**
  - 自动预览会把自己的候选设为基准（`App.tsx:2909-2912`），而卡片上的“Preview”只是查看（`3306-3309`）【静态】。
  - `test_working_draft.py:26-37` 断言了位置移动；`test_candidate.py:698-712` 断言的是 `/api/state` 的参考 run 不变。两者测的是不同概念，并不互相矛盾。真正的冲突在实现与文档规则之间。
- **场景：** 基准是 S1，agent 试了一个更高的雨棚。你看了觉得不行，关掉应用。下次打开后，基准和聊天上下文都成了 agent 的雨棚。
- **处置：** 需要先定语义。例如“只有用户自己的 Sync 结果推进保存位置，生成的候选不推进”。之后单开 lane，前后端一起改。见第 8 节 Q2。

### F2 直接建模和描图作用于屏幕上的模型，底栏却写着另一个基准【静态】
- **实际：**
  - 本地草稿绑定屏幕上的模型，代码注释写明“it asks nobody to press Continue first”（`App.tsx:580-591, 685-692`）。
  - Sync 针对查看中的 run 提交，再把结果设为基准（`2702-2722, 2753-2777`）。
  - 描图基于最后显示的模型（`2392-2426`）。
- **同屏的其他表述：**
  - 底栏：“Next edit starts from ‹基准›”（`WS/features/stage/Stage.tsx:1144-1160`）。
  - Arch 输入框：“Viewing only… before making changes”（`App.tsx:3060-3061`）。
  - 参数锁要求先继续（`2583-2590`）。
  - Board 草图与 Hub 聊天使用编辑基准（`2478-2488`；`docs/PROTOCOL.md:923`）。
- **另一面：** 有浏览器测试明确期望“打开的候选可直接编辑”（`candidatePreview.browser.mjs:474`）。
- **场景：** Hub 在右侧打开 agent 的候选，你推拉一个面并 Sync。新基准就包含了 agent 的改动，而屏幕一直写着下一次编辑从 S1 开始。
- **处置：** 产品决定，见第 8 节 Q1。

### F3 撤销/重做把浏览过的版本变成持久化的编辑基准【复核】
- **实际：**
  - 历史记录的是屏幕上的 run：`loadedArtifact?.runId ?? sourceRunId`（`App.tsx:2556-2577`）。
  - 撤销经 `showRunAsBase` → `changeEditingBase`（`2604-2668`）。
  - `useSession.ts:149-168` 把这个选择持久保存。
  - 撤销提示只在开发者模式可见。
- **场景：** 看了 C1、C2 之后按 Ctrl+Z，C1 就成了保存下来的基准。
- **处置：** 本轮 lane `GH-234/view-base` 在修。

### F4 “从这个版本继续”被拒时看不见，并抛出未处理的 rejection【复核】
- **实际：**
  - 底栏按钮调用 `void onContinueModelSource(...)`（`Stage.tsx:1156`），结果为 null 时抛错（`App.tsx:3389-3391`）。
  - 正忙时直接返回 null。
  - 有未同步草稿时，只写入 `historyError`：一句硬编码中文，仅在 Versions 面板内显示（`App.tsx:1883-1887`）。
- **处置：** `view-base` lane。

### F5 编辑基准保存失败从不提示【静态】
- **实际：** `useSession` 发布了 `persistenceFailed`，但无人读取；现成的 `stage.base.notSaved` 键也没被使用。
- **处置：** `view-base` lane。

### F6 两个聊天【静态】
- **Hub 聊天：** CLI agent，可持久、可归档，带附件（`HUB/ChatShell.tsx:919-955`）。
- **Arch“对话”：**
  - 背后是 `/api/intents` 确定性意图编译，默认 `deterministic`。
  - 只存在内存里，默认关闭。
  - 用 × 关掉后没有控件能重新打开，只会被 Board 反馈、草图等顺带打开（`App.tsx:494-505, 2349, 3240, 3395`）。
- **矛盾之处：**
  - 两个输入框可以同时出现，Arch 的那个写着“No agent is wired here”（`HUB/i18n/messages.en.ts:515`）。
  - Arch 空状态上的“回到对话”，预填的却是 Hub 的输入框（`Stage.tsx:1237-1252`）。
  - 注记说“标记会随句子发送”（`messages.en.ts:773`），但 Hub 聊天上下文不带任何手势（`App.tsx:707-732`）。
- **处置：** 交互方案的核心问题。

### F7 “认可方向”不存在；“接受”的用语纠缠；没有拒绝【静态】
- **认可路由没人用：**
  - `PROTOCOL.md:483-490` 定义的认可路由 `POST /api/proposals/{id}/decision` 没有客户端调用；生成的 SDK 里有（`sdk.gen.ts:728-732`）。
  - 该路由依赖进程内存里的提案和 job（`API/routes/proposals.py:409, 523-541`），运行时重启后就无法使用。
- **Hub 项目卡：** 每个候选下都写着“Candidate — not endorsed, not issued”，这句话恒为真（`ChatShell.tsx:1026-1030`）。
- **接受有四种叫法：**“接受为下一 Stage”“确认下一 Stage”“确认当前模型为 S0”“当前提交”（`VersionsStrip.tsx:105, 125, 131, 159`）。
- **门槛不一致：** 确认 S0 没有审阅门槛，也不写审计事件（`design_history.py:603-674`）；S1 及之后都有（`697-864`）。

### F8 “可审阅 / ✓ held”超出检查实际支持的范围【静态】
- **实际：**
  - 零关系时 held 与 fullyChecked 都为真（`relation_checks.py:63-73`；`project_runner.py:1122-1123`；`candidate.py:955-959`）。
  - 普通模式的结论卡显示“Geometry ✓ / Dependencies ✓ held / nothing confessed”（`VerdictCard.tsx:32-48, 110-165`）。
  - 服务端的保留说明只在开发者模式显示（`VerdictCard.tsx:181`），违反 `PROTOCOL.md:626-636, 771-775`。
  - 这个就绪状态是接受 Stage 的门槛（`design_history.py:751-763`）。
- **相关工作：** 与 P110 相关。

### F9 描图面板声称“没有图像模型解读过此页”，但 agent 可以写同样的页面标注，且标注不记作者【静态】
- **证据：** `DocumentTracingPanel.tsx:52`；`HUBAPI/chat.py:2020, 2529, 2863, 2869-2870`；`API/application/gestures.py:327-354`。

### F10 Arch 里生成的候选 24 小时后被永久删除【静态，待确认】
- **实际：**
  - 每个候选都标记为 `automatic`（`working_draft.py:107-108`）。
  - Hub 每 15 分钟对过期且未被引用的候选执行 `rmtree`（`HUBAPI/runtime.py:46, 813-841, 885-890`）。
  - 在 Hub 聊天里被提到的候选受保护。
  - UI 没有 Exploration、拒绝或归档入口（`client.ts:262-270`）。
- **与文档冲突：** ADR-007:11 写着“old runs — nobody deletes”；`STAGE_BRANCH_CANDIDATE_PLAN.md:97, 116` 写着保留未选中的候选。

### F11 查看历史就会改变编辑基准；跨 Stage 继续失败【静态 / 待确认】
- **查看即改基准：**
  - 点 Stage 就会把它设为编辑基准（`VersionsStrip.tsx:128-131` → `App.tsx:2070-2072`）。
  - 方案规定检出旧 Stage 是只读的（`STAGE_BRANCH_CANDIDATE_PLAN.md:137`）。
- **跨 Stage 继续被拒【待确认】：** 切换时会带上已保存位置的 Stage，服务端以 409 `DESIGN_STAGE_MISMATCH` 拒绝（`useSession.ts:117-118`；`projection.py:244-254`）。
- **历史不刷新：** 设计历史不随后台事件刷新，于是 Accept 以 `DESIGN_BRANCH_STALE` 失败（`App.tsx:487-489, 1043-1052`）。

### F12 命令行正式发布可能发布未接受的候选【待确认，不属于 Hub UI】
- **证据：** `archflow/project/issue.py:142-223` 不检查 harness run；harness 不要求任何检查（`API/adapters/harness.py:74-90`）。
- **与文档冲突：** ADR-007:28-31。

### F13 用语漂移【静态】
- “Stage”同时指设计 Stage 和工作流 stage。
- “issue”被当成版本名词使用。
- Hub 卡片上的 Stage 不带分支名。
- “Version”有两个含义。
- 澄清选项也叫“Candidate”。
- 文档仍写“刷新会丢草稿”，实际上草稿会被恢复。

### 外壳层面的其他矛盾【静态】

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| H1 | README 的右轨描述已过时 | `apps/monkeyhub/README.md:38` vs `ChatShell.tsx:37-43` |
| H2 | Fab 放在“Workspaces”组，却不需要项目 | `ChatShell.tsx:35-43, 643` |
| H3 | Fab 的“Display settings”链接到 `#settings`，没有任何效果；在 iframe 里还会加载第二个 Hub | `FabPage.tsx:121-123` |
| H4 | Fab iframe 打开时就冻结了外观，并且每秒轮询 `/api/apps` | `ChatShell.tsx:677`；`main.tsx:76, 131, 141-147, 196` |
| H5 | 设置对话框的问题（见下） | 见下 |
| H6 | 两套错误呈现，各自的错误码映射互不覆盖；中文界面里出现英文错误 | `chatError.ts:169-170`；`main.tsx:25-35` |
| H7 | 软件更新重启会拦截未发送的草稿，但刷新或关窗不拦；ChatShell 和 3D 工作区都没有 `beforeunload`，Board 和 Fab 有 | — |
| H8 | ErrorBoundary 的“Reload the page”只重置状态，并不刷新 | `ErrorBoundary.tsx:81-86` |
| H9 | 启动与加载一律显示“MonkeyArch”品牌；Board 叫“画板”又叫“白板”；`toolUnavailable`/`toolError` 中英含义不一致 | — |
| H10 | 浏览器后退无效 | — |
| H11 | 启动遮罩是 `position:fixed; inset:0; z-index:9`，可能盖住整个 Hub，包括聊天（CSS 推断） | — |

H5 设置对话框的具体问题：
- 开发者模式和事件流开关放在带“保存”的对话框里，但只在当前会话有效。
- “Project and port changes apply…”指向一个已经不存在的项目字段。
- 运行时的启停在没有配置项目时报 `PROJECT_REQUIRED`。
- `monitorPort` 会被保存，但界面上没有对应字段。

### 工作区层面的其他矛盾【静态】

| 编号 | 问题 | 证据 / 处置 |
| --- | --- | --- |
| W1 | 查看其他模型时提案卡被禁用，这是对的；但提示说它“属于另一个编辑基准”，这是错的 | `Conversation.tsx:230`；`useSession.ts:235-242`；`view-base` lane 修正文案 |
| W2 | 对话栏关掉后打不开；比较结果和候选卡落进关着的栏；候选进度不可见 | `App.tsx:3240, 2198-2214, 3357` |
| W3 | 普通模式下错误只显示通用句；系统行被隐藏；“why”链接失效 | `ErrorPanel.tsx:88-102`；`Conversation.tsx:207`；`ProposalCard.tsx:168-174` |
| W4 | 换了画面之后，澄清选项失效但没有提示 | `App.tsx:534, 1437, 1858-1860` |
| W5 | 拖放提示写“Release to open locally”，实际会上传；导入模型上的拾取给出误导提示 | `ThreeDmViewport.tsx:2202` vs `App.tsx:1190` |
| W6 | 刚建的项目上，第一笔草图可能失败【待确认】 | `App.tsx:2513-2514`；`binding.py:763-788` |
| W7 | Copy 无法触发；Arc、Rotate、Scale、Measure 只能用键盘 | `ModelToolButton.tsx:43-52` |
| W8 | “Choose version (N)”在不同模式下计数的对象不同；设计模式下的候选不能比较 | — |
| W9 | Board 保存冲突后无路可走 | `boardSaveQueue.ts:167`；`Board.tsx:1141`；Board 属于 #270 的范围 |
| W10 | 设计模式的 Versions 等处硬编码中文，其他地方硬编码英文 | — |

## 5. 查证后确认正确的点（不要再报）

1. 查看其他 run 时，聊天提案和 Apply 被正确禁止（`useSession.ts:235-243`；`Conversation.tsx:230`）。只有查看中的模型就是编辑基准时，拾取才进入聊天上下文（`App.tsx:718-725`）。
2. 接受 Stage 时的检查是完整的：
   - 精确比对预期的 head。
   - 要求候选恰好来自该 Stage。
   - 校验回放一致。
   - 重试时返回同一个 Stage。
   - 写审计事件。

   见 `design_history.py:697-864`。
3. 从图纸改设计尺寸，只限于图纸自己在分支 head 上的精确来源（`drawing_plans.py:259-293`）。
4. 迟到或乱序到达的候选，不会替换用户已经选定的视图或已变化的上下文（`App.tsx:2855-2928`）。
5. 会误标非候选的 `SourceChip` 从未被渲染。

## 6. 与 2026-09-04 工作台方案的落地对照

| 方案状态 | 现状 |
| --- | --- |
| A 默认工作台 | 部分：对话默认关闭、版本折叠、证据在开发者模式后面。没有 HEAD 状态行，没有 ⌘K，视口约 43%。 |
| B 选中构件 | 部分：有拾取解析和 Esc；没有视口 Inspector，没有 Space 预览。 |
| C 选中范围内的自然语言 | 主要在对话栏里落地；主路径（Hub 聊天）完全不经过提案。 |
| D 审阅 | 大多未落地：按钮仍是“Apply”，没有 Build/Reject/Revise，没有自动前后对比，没有 Commit to HEAD。 |
| E Flow / Inspect / System | 只有开发者模式的证据抽屉，勉强算 System。 |

组件处置情况：
- `SourceChip` 是死代码。
- 对话栏“可从工具栏收起并恢复”这一项已经倒退：工具栏上没有入口。
- Frame、Options、Program 面板已被删除。

## 7. 当前并发边界

- **开放 PR #270（GH-66，Publish 整合）正在修改：** `ChatShell.tsx`、两个 i18n 目录、`api/client.ts`、`api/generated/*`、`ProjectWorkspace.tsx`、`Board.tsx`、`DrawingCanvas*`、`render/ModelPreview|RenderResults|render.css`、`publish/*`，以及登记表和生成视图。本轮不改这些文件。例外是 `view-base` lane：它只在 `stage.base.*` 区域追加 i18n 键。
- **本轮的 lane：**
  - `GH-60/legacy-closeout`：P108/P115 登记清理。
  - `GH-234/ui-audit`：本文。
  - `GH-234/view-base`：F3、F4、F5、W1。
  - `GH-234/interaction-proposal`：新的交互方案。
- **另外：** Drawing、Render、Publish 的完整流程验收正在只读进行（门宽尺寸固定面板、Render 流程是否跑得通）。结果补在第 9 节。

## 8. 需要用户决定的最少问题

- **Q1（F2）：** 在“查看中但不是编辑基准”的模型上直接建模，应该要求先显式按“从这个版本继续”（与参数锁一致），还是把第一笔编辑当作一次可见的“继续”（与现有候选预览测试一致）？
- **Q2（F1）：** 工作草稿的保存位置，是否只由用户自己的 Sync 结果推进，而 agent 和提案生成的候选都不推进？
- **Q3（F10）：** Arch 里未被引用的候选，是否真的应该 24 小时后永久删除？还是应当保留为备选，由用户显式拒绝或归档？
- **Q4（F6）：** 由交互方案给出建议：Hub 里只保留一个聊天，把当前选择作为上下文附带过去；还是保留两个，但赋予明确不同的角色？

## 9. Drawing、Render、Publish 验收

（进行中，结果完成后补入。）
