# MonkeyHub 日常使用 UI / 交互方案

> 状态：方案，待维护者审阅。本文件只定方案与拆分，不改产品代码；审阅通过后按第 7.4 节建立跟踪 issue。
>
> 关联：[#234](https://github.com/cogco1/MonkeyHub/issues/234)（上一轮 UI 审计，本方案是其延续）·
> [#272](https://github.com/cogco1/MonkeyHub/issues/272)（候选管线不外露、自动跟随工作版本；本方案承担其前端部分）·
> [#254](https://github.com/cogco1/MonkeyHub/issues/254)（LIVE / FROZEN / STALE 语义）
>
> 依据：2026-09-24 在隔离运行的 Hub 上做的界面走查和两份代码核对。走查源码为 `f3f65120`，其前端与本分支基线 `b33c1b3e` 相同，文中行号以此为准。

## 1. 目标与边界

**为什么做这一轮。** 上一轮（#264、#267）统一了字号和附件控件，并让建模、图纸、画板说明各自显示的来源。日常使用中最常碰到的困惑仍在：Agent 改完模型后看的是哪一版、下一次修改从哪一版开始、图纸和画板是否还对应当前模型。#271/#272 从亲手验收得出同样的结论：内部的候选和分支管线漏进了日常操作。

**高频路径**（本轮按此排序）：

1. 在对话里让 Agent 改模型：描述修改 → 结果出来 → 在建模里看 → 接着改或确认。
2. 从当前模型出图。
3. 在画板上批注、讨论并发设计反馈。

渲染和图纸细调以后会更常用；手动直接建模不是本轮重点。

**假设。** 主要在 1440–1920 宽的桌面窗口使用，1280 宽要可用；不专门适配手机宽度。

**边界（沿用 #234 的非目标）。** 不重写前端，不先做设计系统，不另存一份项目状态，不把 Stage 简化成普通文件版本，不为界面方便改后端契约。需要 runtime 支持的部分写成对 #272 的依赖。

**成功标准。**

1. Agent 改完模型后，不打开版本面板也能看到新结果，并直接在它上面继续改。
2. 任何时刻都能看出：正在看的是哪一版、是不是当前工作版本、有没有确认为 Stage。
3. 从当前模型出图、在画板上批注并发反馈，都不需要理解候选、分支或修改起点。
4. 常规使用中看不到原始 ID、哈希、API 路径或导出文件名；这些在详情或开发者模式里仍可查。
5. 中文界面没有英文文案，英文界面没有中文文案（品牌名、单位、文件格式等白名单除外）。

## 2. 现状

走查方式：用当前源码构建前端，在隔离的运行目录里启动 Hub，打开 #234 审计时从归档恢复的两个项目副本，在 1440×900 和 800×600 下逐个打开建模、渲染、图纸、排版、画板、制作、用量、Hub 设置和项目信息。另做两份代码核对：候选与来源管线、跨工作区一致性。走查中没有发送消息，也没有调用模型。

### 2.1 三条高频路径上的问题

| 路径 | 现状 | 依据 |
|---|---|---|
| 对话 → Agent 改模型 | 聊天主文本是工具调用行，如 `studio_request · GET /api/candidates/hub-cand-… · completed` | [ChatShell.tsx:878](../apps/monkeyhub/web/src/ChatShell.tsx#L878) |
| | 从聊天打开的结果只能看；输入框提示「当前仅查看，请选择从此版本继续」，要先点继续才能再改 | [App.tsx:1985](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L1985)、[App.tsx:3026](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L3026) |
| | 在建模页发起的结果却会自动显示并成为修改起点；两个入口规则不同 | [App.tsx:2820](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L2820) |
| | 「此项目」面板的「项目操作」是一排相同的 `candidate · completed / 尚无提交确认`（走查项目中 17 行）；候选区直接显示原始候选 ID | [ChatShell.tsx:1022](../apps/monkeyhub/web/src/ChatShell.tsx#L1022)、[ChatShell.tsx:1027](../apps/monkeyhub/web/src/ChatShell.tsx#L1027) |
| 版本面板 | 按内部存储分区：工作草稿、重点版本、24 小时恢复、设计历史与分支、Stage、候选、历史运行 | [VersionsStrip.tsx](../apps/monkeyhub/web/workspaces/src/features/stage/VersionsStrip.tsx) |
| | 运行卡片以导出文件名为标题，如 `studio-candidate-modeler@1393e2488a29.preview.3dm`，并列「原生导出」「3dm 预览（网格，非精确模型）」；没有缩略图、时间、作者和改动摘要 | 走查 |
| | 底部两行「版本 0 · 当前查看 …」和「下次修改起点 …」；面板盖在视口上，800 宽时模型几乎看不见 | [Stage.tsx:2006](../apps/monkeyhub/web/workspaces/src/features/stage/Stage.tsx#L2006)、[Stage.tsx:1144](../apps/monkeyhub/web/workspaces/src/features/stage/Stage.tsx#L1144) |
| 模型 → 出图 | 图纸只能从已确认的 Stage 生成，工作草稿和候选都不行；空态只说「请先在建模页面接受一个模型 Stage」，没有入口 | [DrawingCanvas.tsx:27](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DrawingCanvas.tsx#L27)、[DrawingCanvas.tsx:210](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DrawingCanvas.tsx#L210) |
| | 来源下拉显示 `{Stage 标签} · {分支 ID}`；来源更新后显示「来源已更新」，要手动重建 | [DrawingCanvas.tsx:215](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DrawingCanvas.tsx#L215) |
| 画板批注 → 反馈 | 发送设计反馈时，把建模的修改起点切到这张图关联的（可能较旧的）模型，没有提示 | [App.tsx:2041](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L2041) |
| | 从画板打开的图页只存在内存里，刷新后丢失；切到建模时被关闭 | [ProjectWorkspace.tsx:50](../apps/monkeyhub/web/workspaces/src/app/ProjectWorkspace.tsx#L50)、[ProjectWorkspace.tsx:73](../apps/monkeyhub/web/workspaces/src/app/ProjectWorkspace.tsx#L73) |

### 2.2 通用问题

- **标题与命名不一致。** `Render`、`Drawing · 图纸`、`Publish` + `Untitled`、`MonkeyBoard`、`MonkeyFab`、`MonkeyMonitor`；加载页一律显示 `MonkeyArch`；图纸和排版在 rail 上用同一个图标。文案同时用了四种机制：中央目录、ChatShell 自带文案、组件内文案对象、行内语言判断。依据：[RenderWorkspace.tsx:177](../apps/monkeyhub/web/workspaces/src/workspaces/render/RenderWorkspace.tsx#L177)、[PublishWorkspace.tsx:143](../apps/monkeyhub/web/workspaces/src/workspaces/publish/PublishWorkspace.tsx#L143)、[DrawingCanvas.tsx:26](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DrawingCanvas.tsx#L26)、[LoadingOverlay.tsx:60](../apps/monkeyhub/web/workspaces/src/app/LoadingOverlay.tsx#L60)、[ChatShell.tsx:40](../apps/monkeyhub/web/src/ChatShell.tsx#L40)。
- **语言混杂。** 中文界面里有 `Tracing Paper`、「发送到 Board」「Review 已发送到 Board」、`Physical`、`Retry`；英文界面里，版本面板的按钮全部是硬编码中文。依据：[messages.zh-CN.ts:161](../apps/monkeyhub/web/src/i18n/messages.zh-CN.ts#L161)、[RenderWorkspace.tsx:181](../apps/monkeyhub/web/workspaces/src/workspaces/render/RenderWorkspace.tsx#L181)、[VersionsStrip.tsx:102](../apps/monkeyhub/web/workspaces/src/features/stage/VersionsStrip.tsx#L102)。
- **无障碍。** 折叠侧栏后，「已归档对话」和「Hub 设置」只剩图标，没有名称；「此项目」按钮在走查中的名称是项目完整路径；输入框的上下文复选框在无障碍树里名为 "on"。依据：[ChatShell.tsx:855](../apps/monkeyhub/web/src/ChatShell.tsx#L855)、[ChatShell.tsx:1058](../apps/monkeyhub/web/src/ChatShell.tsx#L1058)、走查。
- **空态与错误。** 渲染「没有可用引擎」不指向设置；排版提示「在画板选择图纸后放入汇报」没有入口；工具区提示语漏了排版和用量；渲染、排版、画板直接显示原始错误文本，各处的重试按钮叫法不同。依据：[RenderWorkspace.tsx:197](../apps/monkeyhub/web/workspaces/src/workspaces/render/RenderWorkspace.tsx#L197)、[ChatShell.tsx:992](../apps/monkeyhub/web/src/ChatShell.tsx#L992)。
- **小问题。** 发送消息时输入框下方显示「正在连接项目…」；从项目打开用量时不按该项目过滤（#234 审计 B 项，未做）；面板拖拽宽度按 `innerWidth − clientX` 计算，没有扣除右侧 rail（待复现）。依据：[ChatShell.tsx:962](../apps/monkeyhub/web/src/ChatShell.tsx#L962)、[MonitorPage.tsx:95](../apps/monkeyhub/web/src/MonitorPage.tsx#L95)、[ChatShell.tsx:967](../apps/monkeyhub/web/src/ChatShell.tsx#L967)。

### 2.3 根因

界面按数据在内部怎么存来组织，而不是按「我现在在哪、下一步做什么」来组织。第 3 节的核心概念就是为此引入的。

## 3. 核心概念：工作版本

**定义。** 工作版本是下一次修改的起点：当前修改起点，加上尚未同步的本地修改。所有工作区都相对它来说明自己的状态。

| 内部概念 | 默认呈现 | 何时展开 |
|---|---|---|
| 修改起点 + 工作草稿 | 合成「工作版本」；草稿只显示为「N 处未同步修改」 | 一直可见 |
| Agent 候选 | 时间线上的一条：作者、时间、改了什么（取自聊天里的那条请求） | 一直可见 |
| Stage | 时间线上的里程碑「S1 已确认」；「确认为 S2」是单独、明确的一步 | 一直可见 |
| 查看旧版本 | 工作区顶部条变为醒目的「正在查看 S0，不是工作版本」，附「回到工作版本」「从这里继续」 | 只在查看时 |
| 分支、恢复点、运行、导出文件、ID | 收进「更多」 | 需要时，或开发者模式 |
| 图纸、渲染、排版的来源 | 统一为三态：**跟随 / 已过期 / 已固定** | 一直可见 |

### 3.1 Agent 结果何时自动成为工作版本

设当前工作版本为 W，Agent 交回结果 C。

- **自动跟随**需要同时满足：
  1. C 是基于 W 生成的（C 的 base 与 W 已同步的状态一致）；
  2. 没有未同步的本地修改；
  3. C 由当前打开的对话或建模页本身发起，且是这个项目最近一次请求的结果。
- **跟随时**：工作版本更新为 C。如果你正看着工作版本，视口切到 C；如果你在看历史版本，视口不动，顶部条提示「工作版本已更新」。上一版留在时间线上，「退回上一版」一键恢复。
- **不满足时**：只提示「Agent 有新结果」，工作版本不变，由你选择「查看」或「从这里继续」。
- **不变的原则**：查看旧版本永远不改变工作版本；「从这里继续」仍是明确动作；确认 Stage 仍是单独的设计判断。
- **判定来源**：只用 runtime 已提供的字段（候选的 base、当前投影、本地草稿状态）。前端只做推导，不另存「当前分支」。#272 的 resolver 落地后改由 runtime 判定。
- **与现状的差别**：现在从聊天打开的结果一律只看（[App.tsx:1985](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L1985)），在建模页发起的结果已经自动跟随（[App.tsx:2820](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L2820)）。新规则统一两个入口，与 #272 一致。

### 3.2 措辞

| 中文 | English |
|---|---|
| 工作版本 | Working version |
| 查看中 | Viewing |
| 跟随 | Following |
| 已过期 | Out of date |
| 已固定 | Pinned |
| 确认为 Stage | Confirm as Stage |
| 其他尝试 | Other attempts |

## 4. 布局

### 4.1 项目栏自动收起

| 1440 宽 | 项目栏 | 对话 | 工作区 | rail |
|---|---|---|---|---|
| 现在 | 260 | 约 500 | 620（默认） | 60 |
| 建议 | 约 48（图标条） | 约 440 | 约 890 | 60 |

打开任一项目工作区时，左侧项目栏收成图标条；可以钉住保持展开，选择会被记住。

### 4.2 统一顶部条

所有项目工作区（建模、渲染、图纸、排版、画板）共用一个顶部条，分三个槽：

- **名称**：与 rail 上的标签一致（建模 / 渲染 / 图纸 / 排版 / 画板，Modeling / Render / Drawings / Publish / Board），工作区标题里不再用 Monkey* 品牌名。
- **上下文**：相对工作版本的一句话，加状态（跟随 / 已过期 / 已固定）。
- **动作**：该工作区最常用的一两个动作，其余进「更多」。

它替换建模底部的「版本 0 · 当前查看 …」「下次修改起点 …」两行，以及渲染、图纸、排版、画板各自的标题栏。

| 工作区 | 上下文示例 | 状态 | 动作 |
|---|---|---|---|
| 建模 | 工作版本 · Agent · 3 分钟前 · S1 之后第 3 版「把主入口移到南侧，加雨棚」 | — | 历史、确认为 S2 |
| 建模（查看旧版本） | 正在查看 S0 · 9/21 · 不是工作版本（警示样式） | — | 回到工作版本、从这里继续 |
| 图纸 | 平面 A · 基于 S1 · 工作版本已领先 3 版 | 已过期 | 确认为 S2 并更新 |
| 渲染 | 来源：工作版本 · 当前视角 | 跟随 | AI、实景 |
| 排版 | 汇报 1 · 4 页 · 2 张图已过期 · 1 张已固定 S1 | — | 全部更新、导出 |
| 画板 | 讨论板 · 已保存 · 选中页「平面 A」基于 S1 | 已固定 | 上传、更多 |

### 4.3 版本时间线抽屉

- 从顶部条的「历史」打开，在工作区右侧推开视口（约 260 px），不再覆盖模型；工作区窄于 900 px 时改为覆盖。
- 顶部固定「工作版本」卡片：摘要、作者、时间、未同步修改数；动作「存为重点版本」「确认为 S(n+1)」。
- 下面按时间倒序排列：作者（你 / Agent）、相对时间、摘要，有缩略图时显示；悬停出现「查看」「从这里继续」。
- 已确认的 Stage 显示为里程碑行「S1 已确认 · 日期 · 确认人」。
- 不在当前工作线上的结果（基于旧版本，或来自其他对话）收进「其他尝试 · N」。
- 「更多」里放：分支、24 小时恢复点，以及现在的运行与导出卡片（原样保留，给需要核对文件的人用）。

```text
版本历史                                  [关闭]
  工作版本（置顶卡片）
    把主入口移到南侧，加雨棚
    Agent · 3 分钟前 · 2 处未同步修改
    [存为重点版本] [确认为 S2]
  你 · 20 分钟前 · 东立面推出 600
    （悬停）[查看] [从这里继续]
  Agent · 1 小时前 · 二层退台 2.4 m
  ── S1 已确认 · 9/22 · 你 ──
  Agent · 9/21 · 首层随场地转 15°
  ── S0 已确认 · 9/21 · 你 ──
  > 其他尝试 · 2
  更多：分支 · 恢复点 · 文件与导出
```

### 4.4 建模工具条

单行：选择、直线、矩形、推拉、移动 | 撤销、重做 | 更多。平面选择、描图纸、圆弧、自由线等放进「更多」；快捷键不变。

### 4.5 窄屏（低于 900 px）

沿用现有单列布局：抽屉改为覆盖层，顶部条的动作收进一个菜单，上下文文字截断。

## 5. 三条路径的交互

### 5.1 对话 → Agent 改模型

- **过程折叠。** 一轮里的工具调用合并为「过程 · N 步」，默认折叠；展开后用人话（读取工作版本、生成修改、回读验证）。原始请求行只在开发者模式显示。
- **结果卡片。** 每个 Agent 结果一张卡：缩略图、「模型已更新」、状态、对象数与关系检查、相对 Stage 的位置。
  - 已跟随：状态「已成为工作版本」；动作「在建模中查看」「退回上一版」。
  - 未跟随：状态写明原因（「你有 2 处未同步修改」「基于 S0」「来自另一个对话」）；动作「查看结果」，以及「先同步我的修改」或「从这里继续」。
- **输入框目标标签。** 输入框上方显示这条消息会改哪一版，如「修改：工作版本 · S1 之后第 3 版」。设计上下文现在已随消息发送（[ChatShell.tsx:517](../apps/monkeyhub/web/src/ChatShell.tsx#L517)），只是没有显示。
- **查看旧版本时不锁输入框。** 标签变为二选一：「改工作版本」或「从 S0 继续」；选后者等于先执行「从这里继续」再发送。
- **新话题。** 「从项目状态继续（不使用旧对话上下文）」改名为「新话题」（只带工作版本，不带之前的对话），放进 + 菜单；开启时在输入框上显示一个标签。
- **「此项目」面板。** 去掉原始的「项目操作」列表和候选 ID 列表，换成最近 3 个结果（摘要 + 时间）和「打开版本历史」；原始列表放进开发者模式。

```text
[你] 把主入口移到南侧，加一个 3 m 深的雨棚

Agent：已把主入口移到南立面中段，加了 3 m 深的雨棚；东侧楼梯随入口平移 1.2 m。
  > 过程 · 3 步

  [缩略图] 模型已更新 · 已成为工作版本
           93 个对象 · 关系检查通过 · S1 之后第 3 版
           [在建模中查看] [退回上一版]

  [缩略图] Agent 有新结果 · 未跟随：你有 2 处未同步修改
           工作版本保持不变，结果留在历史里
           [查看结果] [先同步我的修改]

输入框  标签：修改：工作版本 · S1 之后第 3 版
        想在这个项目里做些什么？
        [+ 附件 · 新话题]                  Codex · CLI 默认模型 [发送]
```

### 5.2 模型 → 图纸

- **还没有确认的 Stage。** 空态说明「图纸从确认过的 Stage 生成」，显示当前工作版本的摘要；动作「确认为 S0 并出平面」和「在建模中查看」。前者先弹确认框，说明确认 Stage 是设计判断、会记入历史。
- **工作版本领先于图纸所用的 Stage。** 顶部条显示「已过期 · 工作版本已领先 N 版」，动作「确认为 S(n+1) 并更新」。
- **图纸列表每行显示三态**：跟随 S2 / 已过期 · S1（带「更新」）/ 已固定 S1。
- **下拉收起。** 「图纸版本」「出图模型」两个下拉收进「更多」（换一个 Stage 出图、固定到某个 Stage）。
- **生成中切走再回来**，自动选中刚生成的图。
- 从工作草稿直接出图（不经确认 Stage）需要 runtime 支持，列为 #272 之后的工作。

```text
还没有可以出图的确认版本
图纸从确认过的 Stage 生成。当前工作版本：Agent · 3 分钟前「把主入口移到南侧」。
[确认为 S0 并出平面] [在建模中查看]

平面 A · 剖切 1.2 m · 1:100      跟随 S2
剖面 1-1 · 1:100                 已过期 · S1   [更新]
总图 · 1:500                     已固定 S1
```

### 5.3 画板批注 → 反馈

- **打开过期页。** 页顶提示「此页基于 S1，工作版本已领先 3 版」和「更新此页」，让多数情况在批注前解决。
- **仍在旧页上发送反馈。** 发送前弹出明确选择，替代现在的静默切换：「先更新这张图」或「从 S1 修改并发送」，并说明工作版本上的修改会留在历史的「其他尝试」里，不会丢。取消则什么都不切换，标记保留。
- **页卡用版本语言**显示关联模型（「基于 S1 · 9/22」），不显示原始来源。
- **图页刷新后恢复、切到建模不关闭**：放在快修 B3。
- 「把反馈直接发到工作版本」需要 Agent 把旧图上的标记映射到新模型，属于后端 / Agent 语义，列为 #272 之后的工作。

```text
此页基于 S1，工作版本已领先 3 版                     [更新此页]

这张图基于 S1
工作版本已领先 3 版。发送后会从 S1 开始修改，工作版本上的 3 次修改
留在历史的「其他尝试」里，不会丢。
[先更新这张图] [从 S1 修改并发送]
```

## 6. 数据来源与缺口

| 字段 | 来源 | 现状 | 处理 |
|---|---|---|---|
| 摘要（Agent 结果） | Hub 聊天记录：带 `candidateId` 的消息及其前一条用户消息（[chat.py:1871](../apps/monkeyhub/api/monkeyhub_api/chat.py#L1871)） | 已有 | 由 Hub 向工作区传入「候选 → 摘要」映射（前端） |
| 摘要（手动修改） | 本地草稿的命令列表（`LocalDraftDto.commands`） | 已有 | 前端汇总为「推拉 ×2」一类的描述 |
| 时间 | Stage：`acceptance.occurredAt`；草稿：`updatedAt`；Agent 结果：聊天消息时间 | 候选本身没有时间戳 | 没有聊天记录的结果只显示先后顺序 |
| 作者 | 候选 = Agent；草稿 = 你；Stage = `acceptance.actorId` | 已有 | — |
| 缩略图 | 前端在显示某个版本时截取视口小图，本地缓存 | 没有 | 完整覆盖需要 runtime 生成预览图，另开 issue |
| 对象数、关系检查 | `CandidateDto.objects`、`relationChecks` | 已有 | — |
| 变化构件数 | `GET /api/candidates/{id}/compare` | 接口已有 | 按需加载，首版不放 |
| 跟随 / 已过期 / 已固定 | 排版：来源状态；渲染：任务的来源状态；图纸：来源状态 | 已有，各自命名 | 前端统一映射为三态 |

## 7. 拆分与跟踪

### 7.1 主线 A（按依赖顺序；每项一个 issue、一个 PR）

| # | 内容 | 依赖 | 主要文件 | 验收 |
|---|---|---|---|---|
| A1 | 版本语言与工作版本状态：把版本与来源判断从 App.tsx 抽到 `features/versions/`（工作版本、查看中、最近 Stage、领先几版、自动跟随判定）；统一的人话标签；Hub 向工作区传「候选 → 聊天请求」映射 | — | App.tsx、Stage.tsx、ChatShell.tsx、ProjectWorkspace.tsx、新 `features/versions/` | 主文本不再出现原始 ID（由第 8 节检查保证）；现有候选与版本浏览器场景全部通过；没有可见的布局变化 |
| A2 | 统一顶部条、项目栏自动收起（可钉住）、建模工具条单行；删除建模底部两行 | A1 | ProjectWorkspace.tsx、各工作区头部、Stage.tsx、ChatShell.tsx / .css | 五个项目工作区的顶部条一致；1440 宽时工作区不少于 850 px；钉住的选择在重启后保留 |
| A3 | 版本时间线抽屉与缩略图缓存 | A1、A2 | VersionsStrip.tsx、Stage.tsx | 条目没有原始 ID；Stage 里程碑、「其他尝试」「更多」可用；900 px 以上抽屉不覆盖视口；恢复、分支、合并、接受等原有动作仍能到达 |
| A4 | 对话结果卡片、过程折叠、输入框目标标签（含查看旧版本时二选一）、新话题、「此项目」面板 | A1 | ChatShell.tsx、ChatMessageContent.tsx、语言目录 | 结果卡片的三种状态正确；过程默认折叠；查看旧版本时可以发送且目标明确；「此项目」面板没有原始 ID |
| A5 | 第 3.1 节的自动跟随规则与「退回上一版」；统一聊天和建模页两个入口 | A1、A4 | App.tsx、useSession.ts、`features/versions/` | 表驱动测试覆盖第 3.1 节的每个条件；「退回上一版」恢复原修改起点；查看历史时视口不被切走 |
| A6 | 图纸：可执行空态、确认并更新、列表三态、下拉收进「更多」、生成中切走回来自动选中 | A1、A2 | DrawingCanvas.tsx 及相关 | 没有 Stage 时一步出图（含确认框）；过期提示与更新正确；三态显示正确 |
| A7 | 画板：过期页提示、发反馈前的明确选择、页卡版本语言 | A1、A2 | Board.tsx、App.tsx（文档意图） | 旧页发送前必须明确选择；取消时不切换修改起点，标记保留 |

### 7.2 快修批 B（与 A 并行）

- **B1 文案与语言**：中文目录里的英文值（Tracing Paper、发送到 Board、Review 已发送到 Board）；版本面板的硬编码中文；渲染、排版、图纸的硬编码标题；中文界面的英文错误标题；加载页一律显示 MonkeyArch；工具区提示漏了排版和用量；图纸和排版共用 rail 图标。验收：第 8 节的语言目录检查通过；中英界面各走查一遍无混杂。
- **B2 无障碍与键盘**：无名图标按钮；名为 "on" 的复选框；名称是完整路径的项目按钮；快捷键不在无障碍信息里（`aria-keyshortcuts`）；Esc 关不掉「此项目」弹层；禁用按钮不说原因。验收：所有按钮有名称；Esc 关闭弹层；禁用按钮有原因说明。
- **B3 空态、错误与小问题**：渲染「没有可用引擎」链接到 Hub 设置、排版提示链接到画板；统一错误展示（发生了什么、怎么办、重试），不显示原始异常；发送时不再显示「正在连接项目…」；从项目打开用量时默认筛选该项目；画板图页刷新后恢复、切到建模不关闭；先复现再修：面板拖拽偏移一个 rail 宽度、已在默认起点仍显示「返回默认修改起点」、制作页「显示设置」在 iframe 里加载整个 Hub。验收：每项附复现前后对比。

### 7.3 顺序与协调

- A1 先行。A2、A4 在 A1 之后可并行；A2 与 A3 都改 Stage.tsx，先 A2 后 A3；A5 在 A4 之后；A6、A7 在 A2 之后。
- B1 最好先于 A2 合入（两者都改语言目录）；B2、B3 随时可做。
- **与 GH-66 的范围重叠。** GH-66 仍为 active，其 `write_scope` 包含 ChatShell.tsx、两份语言目录、App.tsx、ProjectWorkspace.tsx、Board.tsx、DrawingCanvas、渲染与排版工作区等。A 线与 B1、B3 开工前，需要等 GH-66 释放这些路径，或与其负责人协调缩窄；否则新 lane 标为 blocked，并在 `depends_on` 写 GH-66（见 [CONTRIBUTING.md](../CONTRIBUTING.md)「登记与查看并行任务」）。
- **与 #272。** 本方案只做前端。#272 的 resolver 落地后，A5 的判定改为调用 runtime，A6 增加「从工作版本出图」。

### 7.4 跟踪

- 方案通过后，建 1 个总 issue（正文为本文件摘要和子 issue 清单，链接 #234、#272、#254），以及子 issue：A1–A7 共 7 个，B1–B3 共 3 个（也可合成 1 个）。
- 每个子 issue 开工时按 CONTRIBUTING 登记 `GH-<n>` 卡片与窄 `write_scope`，完成后释放。
- 本文件的 GH-234 卡片在上述 issue 建好后释放。

## 8. 验证

- **每个 PR**：typecheck、build、`archcheck` 与 `archcheck --changed <PR-base>`，以及受影响的单元测试和浏览器场景（chatShell、41 个候选场景、drawingCanvas、boardWhiteboard、renderWorkspace、publishWorkspace 等现有套件）；附 1440 / 1280 / 900 宽 × 中文 / 英文 × 深色 / 浅色截图。
- **两道新的常驻检查**：
  - 无原始标识：浏览器检查在非开发者模式下扫描可见文本，出现 `hub-cand-`、`studio-cand-`、`@` 后接 12 位十六进制、`GET /api/`、`POST /api/` 等即失败。
  - 语言目录：中文目录里与英文原文完全相同的值必须在白名单内。
- **自动跟随判定**做成纯函数，用表驱动测试覆盖第 3.1 节的每种条件组合。
- **成功标准 1–3 各一条端到端场景**，作为总 issue 的验收：
  1. 发送修改请求 → 结果出现 → 不打开历史，直接在新结果上发第二条修改。
  2. 查看 S0 → 顶部条显示查看状态 → 回到工作版本；确认 Stage 后，顶部条与时间线一致。
  3. 没有 Stage 的项目：图纸空态一步确认并出图 → 画板上传该图并批注 → 发送反馈，全程没有原始 ID，没有隐式切换。

## 9. 风险

- **大文件。** App.tsx（约 3.5k 行）、Stage.tsx（约 2k 行）测试密集；A1 先做抽取，缩小后续 PR 的冲突面。
- **自动跟随改变了现有原则。** 「从聊天打开结果只查看」是有意设计（[App.tsx:1985](../apps/monkeyhub/web/workspaces/src/app/App.tsx#L1985)）；新规则靠「退回上一版」和测试兜底。README 中关于 Continue from this version 的说明要在 A5 中补上自动跟随一句。
- **缩略图只在本机缓存。** 换机器或首次查看时显示图标。
- **摘要依赖 Hub 聊天记录。** 从其他入口产生的结果只能显示「Agent 修改」。
- **图纸仍以 Stage 为来源。** 「确认并更新」会让确认 Stage 更频繁，确认框必须说明这是设计判断。

## 10. 不在本轮与现有 issue

| 方案部分 / 相关工作 | Issue | 本轮处理 |
|---|---|---|
| 工作版本概念、自动跟随、三态语义 | [#272](https://github.com/cogco1/MonkeyHub/issues/272)、[#254](https://github.com/cogco1/MonkeyHub/issues/254) | 只做前端呈现与判定；resolver、Worktree Graph 留在 #272 |
| 上一轮 UI 审计 | [#234](https://github.com/cogco1/MonkeyHub/issues/234) | 本方案是其延续 |
| 图纸投影本身 | [#244](https://github.com/cogco1/MonkeyHub/issues/244) | 只改交互，不动投影 |
| 渲染、排版的新功能 | [#253](https://github.com/cogco1/MonkeyHub/issues/253)、[#66](https://github.com/cogco1/MonkeyHub/issues/66) | 只接入统一顶部条和三态 |
| 直接建模工具（旋转、缩放、测量、视图） | [#136](https://github.com/cogco1/MonkeyHub/issues/136)、[#137](https://github.com/cogco1/MonkeyHub/issues/137) | 不在本轮 |
| 首次引导 | [#86](https://github.com/cogco1/MonkeyHub/issues/86) | 不在本轮 |
| 从工作草稿直接出图、反馈直接发到工作版本 | #272 之后 | 不在本轮 |

另：[#271](https://github.com/cogco1/MonkeyHub/issues/271) 与 #272 前 500 行相同，#271 多一节协作 demo；由维护者决定以哪个为核心。
