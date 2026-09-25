# ArchFlow 团队接入与开发环境

**用途：** 给在 ArchFlow V4 上开发、联调和保存项目结果的人一条可执行的最短路径。  

**Coding agent 从第 0 节开始；首次安装环境的队友从第 8 节开始。**
第 1–7 节供涉及内核和项目存储的开发查阅，首次启动不需要先读完。

**规则分工：** `AGENTS.md` 保留少量长期规则，`CONTRIBUTING.md` 说明实际协作流程；
module registry 管软件归口与公开契约，work registry 只管未完成任务和源码范围，policy 管可执行静态检查。
本文是索引和操作指南。发生冲突时，软件归口以
[`governance/module_registry.json`](../governance/module_registry.json) 为准，对外协议以
[`PROTOCOL.md`](PROTOCOL.md) 为准，项目落盘以
[`archflow/project/layout.py`](../archflow/project/layout.py) 与
[`archflow/project/repository.py`](../archflow/project/repository.py) 为准。

下文的实现说明以当前代码和 owner 为依据；本机启动核验与第二位成员实际试用分别报告。

## 0. MonkeyHub 统一入口

**MonkeyHub 打开后直接聊天。** 左侧按项目组织对话，中间保留聊天记录，底部输入需求；
需要查看模型、图纸、画板、制作或用量时，在右侧打开该项目的现有工具页面。
底层调用本机 Codex / Claude CLI，并以原生 session 继续对话；Coding Plan 沿用 Claude CLI
已有的兼容端点配置。用户不再先选择“进入工作区”。项目与运行配置仍由现有 owner 保存。
新 Codex 对话通过锁定版本的 ACP SDK 与上游适配器保持连接，旧 CLI 对话仍可续接。
源码环境的一次依赖安装见 [Hub README](../apps/monkeyhub/README.md#python-entry-and-development)；
权限请求直接呈现在工具活动中，停止会取消仍待回答的请求。
每个项目使用独立的 Project Runtime 进程与端口（代码仍在 `apps/archflow-studio/api`，契约见 [docs/PROJECT_RUNTIME.md](PROJECT_RUNTIME.md)），右侧的 MonkeyArch、MonkeyDiagram、MonkeyBoard 在同一项目内共用这一个进程。
不同项目可以并行聊天与建模；切换项目不停止其他项目，也不改写默认项目配置。已打开的工具页直接切换，保留加载状态。
候选成功读回后立即打开模型，无需等待聊天整轮结束；再次打开同一候选复用页面。
旧对话可以归档并恢复，原消息和原生 CLI 会话保留；运行中的对话需完成或停止后归档。
明确接受一个 Stage 后，从该阶段的已保存编辑来源发送下一条消息，会自动从阶段成果及必要条件建立新的 provider 会话；
普通候选修改继续当前会话。交接发生后聊天显示阶段名称，重开同一阶段不会重复切换。
未同步草稿仍需先保存；历史浏览不改变编辑基底。阶段说明只由现有 Stage/StateRecord 派生，
上游变更会列出声明依赖的影响和待复核项；参数锁不代表整个形体冻结，未声明的跨阶段依赖仍需核查。

已有 3DM 的原生对象查询使用 `GET /api/model-assets/{assetSha256}/index`，明确传入
`runId`、`stateDigest`，先按 `offset` / `limit` 分页，已知对象可重复传入 `objectId` GUID。
来源三项身份从 `GET /api/artifacts` 的同一 `modelSource` 复制，不以文件名或对象名代替。
聊天通过既有 `studio_request` 读取；返回同名和未命名对象、图层、块引用变换及原始属性，
不将它们自动认定为墙、窗或已获准修改的范围。索引不计算逐对象几何摘要、边界或形态，
但每次仍需解码整份 3DM；分页限制返回对象数，不是局部文件解析或端到端加速的证明。
这是 #255 模型接入与 #260 原生项目任务的共用索引入口；任意导入对象的 GUID 到项目实体
绑定、Stage 范围准入，以及从限定修改到冷重开和派生结果过期的整条链路仍需后续切片验证。

已有项目绑定构件的组合模型续改保留被重建对象的原生 GUID；新对象不能借用来源文件中
保留或本轮退役对象的身份。构件覆盖检查在完整组合模型注册前执行，候选重启读回也用
原有修改记录和执行回执复核覆盖，避免运行时失败在重启后变成成功。
这些检查服务于现有编译对象的续改；导入文件里出现一个 GUID 本身不构成修改许可，
也不证明已有依赖图包含用户需要保护的全部对象。

| 使用者 | 同一入口下的操作 | 当前实现 |
| --- | --- | --- |
| 普通用户 | 打开 MonkeyHub，添加已有项目并聊天，按需打开右侧工具页 | 当前源码已接入；已安装候选包需重新构建才包含此界面。新项目仍由现有创建命令建立 |
| 操作已有应用的 Agent | 使用聊天提供的绑定项目与 stdio 工具，按需读取 Studio 某个动作的 schema | 调用既有确定性 proposal/candidate、图纸等接口；完整 Skill 工具箱尚未接入执行 |
| 开发代码的 Agent | 定位源码与任务，再按下表查询 owner 或工具箱契约 | 复用 `devctl module`、`hgs skills list/show`，它们是开发与检索工具 |

已运行的 Hub 以自己的设置和实际健康检查为准，不通过源码目录里的配置猜测其项目。
源码开发单独启动 Project Runtime 时，配置只来自那次启动显式给出的 `-ProjectDir` 与环境变量。

**录一段演示：桌面操作默认关闭。** 需要让 Hub 操作本机桌面（录制方法演示，或操作没有 API 的软件）时，
在该 Hub 运行根目录下手工创建 `diagnostics/monkeycontrol/policy.json`，内容形如
`{"enabled": true, "allowedProcesses": ["notepad"], "mode": "demo"}`：`allowedProcesses`
就是允许被驱动和被读取的全部进程，`mode` 取 `fast` 或可见的 `demo`。Hub 每次请求都重新读这个文件，
开启、扩大或撤销都不需要重启，而且 Hub 只读不写；没有它时 `POST /api/computer/actions` 直接以
`403 COMPUTER_USE_NOT_ENABLED` 回答并指出该路径。回执、截图、录像与可重放的标注投影都写在同一个
`diagnostics/monkeycontrol/` 目录下，不进入任何项目目录，也不是项目状态；演示做完后把 `enabled`
改回 `false`。动作契约、拒绝码、录像目录结构与 `python -m monkeycontrol` 命令行见
[docs/COMPUTER_USE.md](COMPUTER_USE.md)。

### Agent 按任务检索

第一次任务只需要确定：**源码检出位置、此次目标，以及涉及设计时的项目目录**。
源码目录和当前任务通常由宿主提供；设计项目使用上面的 Hub 绑定或独立 Studio 配置。
只读取需要的字段，不把凭据或整份配置放进上下文。未指定设计项目时先完成源码查询，
不要通过扫描磁盘猜测项目，也不要自动创建一个临时项目。

首次进入时检查一次当前 Git 分支、HEAD 和 working tree。后续按下面的次序工作，
复用已确定的位置和 owner；只有任务切换、配置改变或出现相关错误时才重新定位。

| 当前需要 | 固定入口 | 接着读取什么 |
| --- | --- | --- |
| 了解 ArchFlow 做什么 | README 的概述与当前状态 | 涉及架构决定时才读 `docs/ARCHITECTURE.md` 的对应部分 |
| 开始或接续一项工作 | 对应的 GitHub Issue，再运行 `python tools/devctl.py work` | Issue 记录需求、验收与讨论；`work` 列出登记中的 active／review lane（谁在改哪些路径）和不占路径的 legacy 卡。[P115](mapping/planning/P115-capability-consolidation.md) 是冻结的历史索引，不从中推导当前任务，也不回填进度 |
| 按目标查已有操作 | `python tools/devctl.py capability <目标或能力-id>` | 读取匹配项的范围与入口；绑定项目内使用 `GET /api/capabilities?goal=...`，再描述具体来源和目标。首项为已有对象的数值修改；已支持和缺少的部分见返回的 `works`／`missing` |
| 查建模、图纸、项目或应用的代码归属 | `python tools/devctl.py module <关键词>` | 用返回的精确 module id 再查契约；按 `--section`、`--offset` 补齐被省略的相关项 |
| 修改已有实现 | owner 的 `source_paths`、`public_api`、`tests` | 目标实现及真实调用方；只有存在具体疑问时才在相关包中 `rg` |
| 查看全部登记项 | `python tools/devctl.py status` | 每项一行；blocked 的 legacy 卡不占路径，卡片状态不代表能力可用性 |
| 创建或复用源码 worktree | `python tools/workspace.py create --branch codex/<task>` | 从一次配置的开发根取得目录；已有任务继续使用其原检出，详见下方“开发目录只配置一次” |
| 查看打包目录或构建候选包 | `python tools/package_monkeyapps.py --show-paths` | 共用开发根配置；确认来源后用 `--source-ref <ref>` 构建，仍需打包工具所需的 Node/npm |
| 查共享工具箱 Skill | `hgs skills list <关键词> --path <toolbox-root>/skills` | `hgs skills show <id> --path <toolbox-root>/skills`，再按需读取示例或调用入口 |
| 创建新的设计项目 | `python tools/create_project.py --project <外部项目目录>` | 第 8.3 节；已有项目直接打开完整目录，不重新初始化 |
| 运行或配置 Studio | 第 8 节与 Studio README | 对应配置、启动命令和 API；模型内容通过既有项目读取入口取得 |
| 查看打包目录或构建候选包 | `python tools/package_monkeyapps.py --show-paths` | 复用第 1.1 节的一次目录配置；源码仍取明确的 Git 版本 |

两张能力表承担不同用途：ArchFlow 的 `governance/module_registry.json` 登记软件 owner 及已有能力的目标、范围与入口；
共享工具箱的 `skills/*/skill.yaml` 登记工作流与入口，`generated/skills/index.md` 是它的生成视图。
`hgs` 使用工具箱自己的安装环境，`--path` 显式指定其已有源码目录，避免依赖当前目录。
没有安装工具箱时仍可开发 ArchFlow；需要某项 Skill 时再按工具箱 README 安装或读取那一项 manifest。
外部引用、入口已安装、命令可执行是不同状态，不能把登记项直接当作已接通的运行能力。

这条流程不要求每次读取完整 SYSTEM_MAP、整个 registry、所有 SKILL.md 或历史会话。
例如修窗洞代码先 `module opening`，选定 owner 后读取具体契约与实现；不要先翻历史建模脚本。
使用已有建模能力时直接调用已接通的 CLI / API，按需读调用契约；正常改稿不要求读取实现源码。
工具返回“没有匹配”时，使用同一能力的其他关键词或已知 API/path 查询，仍从登记入口收窄。

**每次执行沿用三个位置：** 源码根放实现；运行环境根放 Python、依赖和配置；
设计项目根放输入、候选和成果。新项目通过创建命令建立一个 P036 根，之后每次修改产生该项目中的
run，不为每个脚本版本再建项目。程序从 `ProjectLayout` / `RunLayout` 取得区域路径；
CAD 导出共用 `cad_workspace_path`，默认进入 `runs/<run-id>/workspaces/cad-<stage>-<seat>/`。
调用方准备目录，runner 使用明确的绝对 workspace；不以当前 shell 目录兜底。
图纸、截图和保留记录继续使用各自已登记的 P036 入口；临时可重建文件才使用显式 cache/temp。

**当前聊天入口：** CLI 收到绑定项目和少量工具说明。修改已有构件的数值时，先用
已知的候选来源和目标直接读取能力详情；首次不清楚能力或对象时，再查询
`GET /api/capabilities?goal=...` 或项目状态。沿详情返回的请求执行时，`studio_request` 的
`awaitSeconds` 可让 proposal 候选或一次数值修改在同一次工具调用内提交、等待任务完成，并行读取候选和比较结果。
多个已确定的形体通过 sketch 的 `sketches` 一次提交；确定性的连续修改通过 `sourceProposalId` 在内存中累积，保持该链原始 `baseStateDigest`。
下一步设计决策依赖实际结果时，可先生成并观察候选；后续从 `GET /api/state?run=<candidateId>` 读取准确状态，写入时使用该状态的 `stateDigest` 和 `sourceRunId`。
同一 Stage 内允许多轮候选观察与修改，不自动接受或发布。修改方法、schema 查询和状态刷新按实际需要选择。
明确锁定或解锁参数使用 `POST /api/proposals/parameter-locks`，提交准确来源、`stateDigest`、
`parameterKeys` 和 `action: lock|unlock`，再通过既有 proposal candidate 入口留存，并从返回的候选继续。
提案尚未执行时不产生持久锁，也不能并入 `sourceProposalId` 编辑链。锁保存于现有 StateRecord/P036，
新会话 ContextPack 会读到；普通编辑不能清锁、删除既有绑定或将其换成常量／其他参数，新增引用与未锁字段仍可修改。
这是参数范围的约束，不是整个体块冻结，也不代表 Stage 接受。配置 actor 认证时，锁与解锁使用现有 `accept` 权限；
默认本地无认证模式只能归属于本地调用边界，不能据此证明自然人操作。该动作不提供给聊天 MCP。
需要共享尺寸或联动的设计，通过同一个 `POST /api/proposals` 提交 `semanticEdit`：当前 Agent 直接写入已有的构件、参数、表达式和关系契约，不再调用第二个模型。
`utterance` 与 `semanticEdit` 二选一；后续可只提交 `{key, value}` 参数更新，保留原有表达式和几何中的 `@key` 绑定。
prism/planar-surface 的轮廓坐标支持参数引用；渐变截面形体可使用已公开的单个 `loft`，当前支持顶点对应的闭合折线截面及 normal/straight 两种方式。
`studio_schema` 的 `producer` 选项可只查询所需 producer 的请求契约，避免读取无关几何与重复响应字段。
候选读回直接返回保留 inspection 的对象包围盒、单位和坐标系；首次候选没有上一 run 时不请求比较，inspection 缺失会明确说明。包围盒和技术检查不代替视觉检查或空间意图验收。
已有标高编辑 `POST /api/proposals/elevation` 可通过聊天 MCP 调用并查询 schema，沿用项目绑定、准确来源和 keep 条件。
大型参数提案的即时回复只列前 100 条变更和直接影响，明确总数、省略数及完整提案读取路径；冲突、锁、keep 和覆盖限制保持完整。
现有出图入口支持四向立面与未剖切的顶投影；文字和尺寸可通过已有文档批注接口绑定到准确页面，沿用其版本比较保存。
`awaitSeconds` 限定为提交成功后的等待时间；超时或读取失败返回原任务的只读续查入口，不重复提交。
未指定该选项时保持原来的单请求行为，Studio 的候选 HTTP 接口仍异步返回。
其他动作按需查询现有 Studio schema。能力详情使用实际来源、目标字段和 keep 范围，请求结构继续引用 OpenAPI。
每次调用核对 Hub、Studio 与聊天的项目绑定，执行继续经过已有 API；聊天记录与 CLI session id
保存在 Hub runtime 的 `chats/`，建筑输入与结果仍在项目根。共享工具箱的自动检索与执行仍待接入，
开发源码时继续按上表查询 owner。新成员的实际试用仍应验证首次配置、一次真实候选以及同项目续改。

**与 Codex 的接入方式比较：** Codex 在启动时按固定位置发现
[项目指令](https://learn.chatgpt.com/docs/agent-configuration/agents-md)，先呈现
[技能名称与描述](https://learn.chatgpt.com/docs/build-skills)，选中后才读取正文；
[本地环境配置](https://learn.chatgpt.com/docs/environments/local-environment)可复用 worktree 设置与常用动作。
这些机制将环境准备和入口发现放进宿主，减少每次交给模型重新调查的工作。
对 ArchFlow 的推论是：Hub 在接入时应提供简短的绑定结果和可执行入口，长指南只供安装、开发和排错查阅。
普通使用者应沿已有 MonkeyHub 安装包的“打开应用 → 选择项目 → 提交任务”入口；
上面的源码查询是开发路径。安装包已包含运行环境，仍需验证新用户的第一次实际项目操作是否顺畅，
不能把文档和入口存在等同于已完成开箱试用。

## 1. 先建立正确的三层物理边界

### 1.1 源码仓不是项目数据盘

```text
Git 源码仓 / worktree
  archflow/                  公共项目底座、建筑事实和技术接口
  monkeyarch/                三维建模算法与运行编排
  monkeydiagram/             图纸投影与表达
  apps/archflow-studio/api/  项目运行时；API-only，历史目录名保留
  governance/                owner、依赖与架构防火墙
  docs/                      人读文档，不是实时项目状态
  tests/                     测试代码；运行时只产生可丢弃的临时输出
  probes/                    经明确审查后提交的小型机制证据

外部运行根（不进 Git；由调用方按用途显式提供）
  <workspace_root>/projects/<project_id>/   活跃项目
  <cache_root>/                             可重建缓存
  <temp_root>/                              可删除临时数据
```

活跃项目与 `probes/` 使用同一种 P036 项目格式；它们只因是否被明确提升进 Git 而不同，
不得为外部项目再建第二个数据库或写入器。通用说明见
[`archflow/project/README.md`](../archflow/project/README.md)。项目的选择属于 MonkeyHub：Hub 的应用设置（`<runtime root>/config/applications.json`）为每个 Studio
实例指定单个 `project_dir`、CAD 后端与参考 run，启动 Studio 子进程时注入环境变量；Studio 自身没有
与之并行的持久配置。开发时直接启动用 `scripts/dev/run-project-runtime.ps1 -ProjectDir <项目目录>`，
项目目录必须显式给出。开发工具通过下述一次配置取得工作区、缓存与临时目录。

缓存和临时根不是项目记录。能被删除而不改变设计含义的内容才可以进入那里；证据、模型、
验收回执和恢复所需数据不能借 `temp` 绕过项目存储。

项目目录名与 `project.json` 的 `project_id` 一致。首次建立使用
[`tools/create_project.py`](../tools/create_project.py)，不手工拼装 `HEAD` 或运行记录。
当前创建命令写入项目标识、版本 0 的 `HEAD`、初始化事件与快照，以及
`input/runner/state-record.json`；指定分工输入时再写入 `input/runner/seats.json`。
它同时准备 `objects/sha256/`、`runs/`、`exports/` 等区域，但不会生成 run 或模型。
Hub 启动时自动准备 Monitor；新建、连接或切换项目后调用 `POST /api/project/modeling`，
一次启动或复用建模、图纸、画板共用的 Studio，等待服务就绪并核对服务与项目身份。
该入口为真正空的项目准备建模根、零标高和建模分工，保留已有设计、画板和上传资料。
准备失败保留已创建的项目，重试继续准备同一项目；工具页面首次打开才加载，后续切换保留页面。
项目列表同时读取已配置工作目录中的真实项目，模型连接失败不会让已创建项目在刷新或重启后消失。
源码版本更新后需重启整个 Hub，使 Hub 和新启动的工具使用同一版本。独立 API 客户端可调用同一入口，
随后读取 `/api/state` 和 `/api/state/frame` 创建首个 sketch 候选；首次请求省略 `sourceRunId`。
各项产物沿 P036 的目录规则保存，子区域随相应操作出现；历史项目是否可用以仓库读取结果为准，
不以所有空目录是否齐全判断。

原始 Rhino 文件可以留在设计师原有的工作目录。需要随项目重开、审阅或共享的模型副本、
导出文件和记录，通过既有 P036 入口保留到绑定项目；外部原文件路径不代替项目内的工件引用。

长期源码按 **ArchFlow 公共底座、MonkeyArch 建模业务、MonkeyDiagram 图纸业务** 分清目录与依赖。
当前代码所在位置与目标归属的具体映射只维护在 [REPO_LAYOUT.md](REPO_LAYOUT.md)；
迁移随真实调用链进行，不在安装时复制三套代码，也不让每位成员自行决定目录结构。

#### 开发目录只配置一次

[`tools/workspace.py`](../tools/workspace.py) 和 [`tools/package_monkeyapps.py`](../tools/package_monkeyapps.py)
共用个人 Git 设置 `archflow.package.workspace-root`；沿用原有键名，避免多一份工作区配置。
首次安装或明确更换根目录时配置一次。下面的绝对路径只是示例，按本机实际位置替换：

```powershell
python tools/workspace.py configure --root D:/MonkeyHubRuntime
```

之后从已有源码检出运行固定入口：

```powershell
python tools/workspace.py paths
python tools/workspace.py create --branch codex/window-edit
```

新分支默认从调用源码的已提交 `HEAD` 建立；约定其他基线时追加 `--base <ref>`。
重复 `create` 返回该分支已有的同一 worktree，保留两边的未提交修改；已有分支不被重置。
如果分支已在旧目录检出，命令报告原位置，继续使用那个目录，不自动搬迁。
旧检出尚不包含该工具时，可调用已有工具的绝对路径，并用前置 `--source-root <源码检出>`
指定操作对象，不复制工具到每个任务。新 worktree 只含选定提交，尚未提交的工具改动不会随之出现。

默认任务名来自分支名，例如 `codex/window-edit` 对应 `codex-window-edit`：

| 用途 | 固定位置 |
| --- | --- |
| Git 源码 worktree | `<root>/workspace/worktrees/<task>` |
| 打包暂存 | `<root>/temp/package-monkeyapps/<task>` |
| 候选安装包 | `<root>/packages/<task>` |
| 共用可重建缓存 | `<root>/cache/package-monkeyapps` |
| 已有设计项目 | 继续使用已配置的项目根，例如 `<root>/workspace/projects/<project_id>` |

进入任务 worktree 后，打包会使用相同任务名和根目录：

```powershell
python tools/package_monkeyapps.py --show-paths
python tools/package_monkeyapps.py --source-ref HEAD
```

任务目录跨次执行复用，每次构建仍在该任务暂存目录下创建独立候选。
只有需要自定义名称时才传 `--task <name>`，创建和打包均使用同一名称。
打包仍支持显式 `--staging-dir`、`--output-dir`、`--cache-dir` 覆盖；一次性覆盖不改保存的根。
原 `--configure --workspace-root <root>` 打包配置命令继续可用。
这些命令管理自己的目录写入，不拦截任意 shell 命令，也不迁移旧目录或初始化设计项目。

### 1.2 `main`、Git worktree 和项目 `HEAD` 是三件事

- Git 的 `main` 是**源码版本线**。
- Git worktree 是同一 Git 仓库历史上的一个独立检出目录。不同 worktree 的未提交文件互不
  自动出现；要进入 `main`，仍需明确提交并合并或摘取。
- 同一物理检出目录中的多个代理会立即看到彼此的未提交文件，也会互相污染暂存区。
- 项目根内的 `HEAD` 是该项目的**已发布设计位置**，不是 Git 分支，也不随源码合并移动。

共享同一物理检出目录时执行以下纪律：

1. 开工时确认 `git status --short`、当前分支和 `git worktree list`。
2. 只编辑自己负责的明确路径；看到不相关脏文件就保留。
3. 只用显式文件路径暂存；禁止 `git add -A`、`git add .` 或目录级兜底暂存。
4. 提交前再次核对暂存 diff；别人的 WIP 不提交、不格式化、不回退。
5. 一个 worktree 完成不等于 `main` 已更新；以 `main` 实际提交图为准。

## 2. 按任务查询，再读对应实现

新增或修改功能前，按这个顺序读取：

1. [`AGENTS.md`](../AGENTS.md)：项目级硬边界。
2. `python tools/devctl.py module <关键词>`，再用精确 module id 读取 owner、公开契约、源码和测试路径。
   需要补充架构背景时读 [`ARCHITECTURE.md`](ARCHITECTURE.md) 或 [`SYSTEM_MAP.md`](SYSTEM_MAP.md) 的对应部分。
   拟议能力不当作已实现；不把整张地图作为每次任务的前置输入。
3. 目标实现与真实调用方；只在这些信息不能解答具体问题时扩大搜索。
4. 只有需要理解旧合并决定时读 [`CANONICAL_SPINE.md`](CANONICAL_SPINE.md)。它是历史决策，
   其中迁移顺序不可重跑，历史统计不是实时状态；实时 owner 仍以 registry 和代码为准。
5. [`DYNAMIC_MAP.md`](DYNAMIC_MAP.md)：尚未完成的工作卡，不是已交付能力清单。
6. 涉及客户端时再读 [`PROTOCOL.md`](PROTOCOL.md) 与
   [`apps/archflow-studio/README.md`](../apps/archflow-studio/README.md)。

先复用已有能力。模块 owner 表示软件职责，可以包含多个实现文件，不是个人或必须塞满的单个文件。
新增独立领域能力可以有自己的目录或外部包，经已有接口接入；同一职责已有实现时不再复制第二套。
归口、公开契约或列出的测试变化才更新 module registry，不逐个登记内部函数。
工作卡只跟踪未完成任务；模块的 `canonical` 标签不表示项目 `HEAD` 或软件版本已经发布。

## 3. 项目目录：每个东西只有一个家

```text
<project-root>/
├─ project.json
├─ HEAD
├─ input/
│  └─ runner/
│     ├─ state-record.json
│     ├─ seats.json
│     └─ program-sheet.json
├─ objects/sha256/
├─ events/
├─ canonical/
├─ runs/<run_id>/
│  ├─ run.json
│  ├─ records/
│  ├─ branches/
│  ├─ candidates/
│  ├─ reviews/
│  ├─ workspaces/
│  └─ recovery/
└─ exports/
```

| 区域 | 职责 | 不能被误解为 |
|---|---|---|
| `project.json` | 不可变的 `project_id` 和格式版本 | 当前设计状态 |
| `HEAD` | 唯一已发布版本指针 | Git HEAD、最近 run、最近文件 |
| `input/runner/` | 设计师正在编写的 State Record、seat pack、program sheet | 已共享或已发布结果 |
| `objects/sha256/` | 经项目存储模块导入的不可变二进制内容 | 任意文件仓或浏览器下载目录 |
| `events/` | 已接受的 canonical 事件链 | Studio 临时事件流 |
| `canonical/` | 每次 issue 形成的可验证快照；旧快照保留 | 可手改的“最终版”文件夹 |
| `runs/<run>/run.json` | run 身份、精确 base 和生命周期 | canonical 版本 |
| `records/` | 该 run 保留的注册 record kind、检查和边界回执 | 任意 JSON |
| `branches/` | run 内的备选分支材料 | Git branch |
| `candidates/` | 候选包和计划 | 已接受设计 |
| `reviews/` | 硬性、承诺或审美审查 | canonical 决策本身 |
| `workspaces/` | 与该 run 绑定的 CAD、模型、截图等过程文件 | OS temp；它会长期保留但仍非 canonical |
| `recovery/` | 外部世界与项目记录的对账、恢复材料 | 普通日志 |
| `exports/` | 项目正式外化、交付或分享包 | 设计事实的唯一来源 |

### 3.1 record、receipt、ref 和物理文件

- **record** 是项目保留的结构化事实。`put_json` 只接收
  [`project.record_kinds`](../archflow/project/record_kinds.py) 已登记的 kind，并返回
  `ProjectRecordRef`。
- **receipt** 只证明一次跨边界行为，例如持久化写入、外部调用、CAD 导出、验收决定或 issue；
  receipt 保留时通常也是 `records/` 中的一种 record。确定性内存转换不自造 receipt。
- **ref** 绑定项目、run、相对路径和内容身份。稳定记录不得把宿主机绝对路径当身份。
- **物理文件**不自动成为 Studio artifact。当前 Studio 的模型列表只接受 run 中执行回执所证明、
  且读取时 SHA-256 仍匹配的文件。
- `ProjectArtifactRef` 能指向一个 run 文件，并不等于它已进入上述 receipt-certified artifact
  列表。检查截图就是这个差别的明确例子。

## 4. 文件落点：先判断归属，再生成

| 正要产生的内容 | 当前正确落点 | 权威状态 |
|---|---|---|
| 设计师正在编辑的 record / seats / program | `input/runner/` 的三个固定文件；只由对应 owner 读写 | WIP |
| 外部原始证据或请求 | 项目 `input/`；需要不可变二进制身份时由项目存储模块的 `ingest` 进入 `objects/sha256/` | 非 canonical 输入 |
| 推导、检查、模型调用、CAD 执行或验收回执 | 产生它的 `runs/<run>/records/`，通过 `put_json` | Shared record |
| CAD `.3dm`、IFC、中间模型、脚本和读回文件 | 产生它的 `runs/<run>/workspaces/<seat-or-purpose>/`；适配器只可写调用方显式分配的 workspace | Shared workspace |
| 与已加载 run 一一对应的 Studio 过程截图 | `runs/<run>/workspaces/studio-captures/viewport-<sha256>.png`，请求只给 `runId` 和 PNG，服务端命名 | 非 canonical inspection |
| 当前项目的正式外化模型、渲染、交付包 | `exports/`，并保留指向来源 run/record/digest 的外化清单 | 非 authoritative package，来源仍在项目内 |
| 跨项目汇报或一次性给人看的汇总包 | 操作方显式指定的项目外分发目录；必须带来源 project/run/ref 清单 | 分发副本，不是项目状态；当前没有受管 `output_root` |
| 可重建下载、编译缓存 | 外部 `cache/` | 可删除 |
| 一次性诊断缓冲 | 外部 `temp/` 或测试临时目录 | 可删除且不得作为证据 |
| Studio 进程日志 | `apps/archflow-studio/.runtime/`（git ignored） | 本机诊断，不是项目证据 |
| Web 同步和构建产物 | `apps/monkeyhub/web/workspaces/.generated/`、`dist/` | 可重建 |

**旧规则的分层修正。**
早期工作区的 `GENERATION_RECORD_SPEC.md`（不在本仓库内）第八节原本把“预览模型、截图、审查包”统一
路由到该工作区的 `output/`。那是 2026-08-30 的跨项目交付/过手规则，粒度不足以表达后来增加的
project-bound Studio capture；该表现已按当前项目存储、protocol、registry 和 API 工作树实现拆分为：

- 能明确归属于一个已有 run 的过程截图，进入该 run 的 `workspaces/studio-captures/`；
- 从一个项目正式外化的结果，进入该项目 `exports/`；
- 只有跨项目汇总、会议分发副本或不拥有项目状态的过手包，才进入操作方显式指定的项目外目录。

当前配置没有 `output_root`；工具不得从 `workspace_root` 猜其父目录。项目外分发文件不能反向成为
canonical 事实。这只是把既有三种用途分开，没有建立新项目存储体系。

以下位置不得承载项目持久状态：浏览器 `Downloads`、源码 `archflow/`、`tests/`、`docs/`、
仓库级 `.runs/`、未分配的任意绝对路径，以及与当前项目或 run 不一致的另一个项目目录。

### 给人使用的产出文件名

项目 `exports/` 或显式指定分发目录中的成果采用：

```text
YYYYMMDD[-NN]_项目名称[_内容或图种][_RNN].扩展名

20260906_罗浮山_南立面模型检查图.png
20260906-02_罗浮山_一层平面图_R03.pdf
```

方括号表示可选段，不写入文件名。“年月日*_项目名称”中的 `*` 约定为可选的同日批次号：
单批次省略，同日需要区分新批次时加 `-02`、`-03`。日期为生成或导出日期，月、日补零，
同批统一日期与批次号。项目名称使用已确定的名称或短名，可保留中文；字段间用 `_`。
内容、图种或图号按区分需要添加；`R01`、`R02` 表示对应内容的修订，不表示已经批准或 issue。
已有同名成果需要保留时增加批次或修订号，不静默覆盖。项目已有明确交付命名要求时优先沿用。

日期顺序和补零参考 [爱丁堡大学文件命名指南](https://data-protection.ed.ac.uk/records-management/practical-guidance/naming-conventions)，
具体分隔符与修订格式是本项目约定。遵守 [Windows 文件名限制](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)：
不用 `< > : " / \ | ? *`、控制字符、保留名称或结尾空格/句点；字段内非法字符以短横线替换，
不靠大小写区分两个文件，也不把显示名中的 `｜` 用作文件分隔符。

此规则只约定允许自定义名称的成果或分发副本，不改变上表落点及来源引用。
`project.json`、`HEAD`、runner 固定文件、run/record ID、内容寻址对象与
`viewport-<sha256>.png` 继续按协议命名；原始输入和历史产物不批量重命名。

## 5. Project Runtime：一条 API 边界，三层权责

```text
React / Vite / three.js / rhino3dm-wasm
  apps/monkeyhub/web/workspaces
                │ OpenAPI-generated SDK
                ▼
Project Runtime（FastAPI）
  apps/archflow-studio/api/archflow_studio_api
  transport → routes → application → adapters
                │ 调用现有 Python owner / 项目存储接口
                ▼
ArchFlow kernel + 项目存储
  archflow/state, capabilities, compilers, validation, project
                │
                ▼
显式绑定的 <project-root>
```

浏览器层只有 MonkeyHub：Board 和 Arch 直接渲染，Diagram 是 Board 内的图页编辑器；本节的 API 边界与三层权责不变，进程契约见 [docs/PROJECT_RUNTIME.md](PROJECT_RUNTIME.md)。

### 浏览器 `web/`

- 负责交互、视口、显示状态和本机 UI 偏好。
- 通过 `web/src/api/generated/` 的 SDK 调 API；不得手写一套平行 DTO。
- 不导入 Python kernel，不选择项目文件路径，不做 canonical 写入。
- 浏览器推导的 mesh、选中状态或截图 Blob 都不是项目记录；需要保留时交给 API。

本地用户默认值通过 `GET/PUT /api/settings/user` 保存语言、主题、字号、intent provider、model 和 timeout；
`developerMode` 是额外的浏览器本地偏好。`GET /api/settings` 读取进程运行设置，不是用户默认值的保存入口。

### Project Runtime `api/`

- 负责请求验证、transport DTO、统一错误体、鉴权/CORS、SSE、任务生命周期和 HTTP 资源。
- `StudioSettings.project_dir` 或 `--project-dir` 必须显式绑定一个带 `project.json` 的项目目录；
  代码没有默认项目根。
- application 层组织用例，也可调用独立领域包；已有设计状态、依赖闭包、几何编译、验证和 issue 仍调用对应 owner，不在 BFF 复制实现。
- 有项目写入时必须调用 项目存储接口/repository，不能在 route 或 application 中直接
  `Path.write_*`。

### 内核 / 项目存储

- kernel 决定语义、依赖、编译和验证；项目存储模块是项目文件系统唯一通用 writer。
- `ProjectLayout` 只命名路径，不创建、不写入。
- `PersistenceDestination` 先绑定 area/run/branch；producer 未获 destination 就停止。
- CAD adapter 的例外仍受控：它只能在调用方已经分配的 speculative workspace 内写，随后由
  runner 保留执行和读回证据。

**已验证：** 当前 Studio 有读取、候选 run、program sheet（仅 local mode 可按请求写 WIP）、
以及其他 protocol 资源，但没有 issue/canonical API。  
`POST /api/captures` 与项目存储的 `WorkspaceSink.put_workspace_file` 已在上述本地主干实现；
它写命名 run 的 workspace、按内容命名、不进入 `/api/artifacts`、不改变 `HEAD`。

## 6. canonical 与 inspection 的边界

项目目录使用 ADR-007 的四种 container state：

```text
input/runner           Work in progress
       │ run_project
       ▼
runs/<run>/            Shared（协调或可供审批）
       │ issue_run；必须精确 base + SATISFIED closure + exit binding + seats complete
       ▼
HEAD                   Published（唯一已发布位置）
       │ 后续 issue
       ▼
旧 canonical snapshot Archived（保留，不删除）
```

唯一 canonical 路径是
[`project.issue.issue_run`](../archflow/project/issue.py)：先完整校验 run，再调用项目存储模块的
`prepare_transition`，最后对 `HEAD` 做 `compare_and_swap`。直接写 `HEAD`、`canonical/`，或因为“测试
都过了”就把一个 run 当作当前设计，均不成立。

以下操作明确**不改变 canonical**：生成候选 run、写 run record、CAD workspace、视口截图、
对比/审查、导出分享包、浏览器本地状态。它们可以提供 issue 所需的证据，但没有 issue authority。

## 7. 新功能的最小搭建流程

### 第 1 步：把需求写成一个可验收动作

先回答四个问题：谁触发、读什么、写什么、完成时用户能观察到什么。若有输出，先在第 4 节表中
找到唯一落点；找不到就停下来请项目 owner 决定，不先写临时目录。

面向用户的模块显示名采用“领域｜具体任务或输出”，用用户熟悉的动作、对象和图种说明用途，
如“建模｜固定视角模型检查图”“出图｜建筑、室内与家具图纸制作”；不要只写“阶段出图”。
这是项目约定，任务用词参考 [GOV.UK 服务命名指南](https://www.gov.uk/service-manual/design/naming-your-service)。
显示名不充当代码身份：注册表继续用现有 `module_id`（如 `adapters.cad_execution`）、
`owner_path` 和 `public_api`；花果山 Skill ID 则沿用自己的 kebab-case，不跨体系统一改名。
Python 代码按 [PEP 8](https://peps.python.org/pep-0008/#package-and-module-names)：模块短小、小写，
必要时用下划线，函数用 snake_case，类用 CapWords。显示名修订不迁移旧 ID、API 或引用，
也不为命名另加注册表字段。

#### 产品名称与版本用语

应用标题、桌面入口和团队文档统一采用下表的英文名称与大小写；中文用途作为说明，
不替换产品名。上面的“领域｜具体任务或输出”用于能力显示名，不用于给应用另起名称。

| 名称 | 用途与边界 | 当前代码标识或入口 |
| --- | --- | --- |
| **ArchFlow** | 共享项目底座、建筑事实、技术契约与正式发布 | `archflow/` |
| **MonkeyHub** | 唯一对外应用入口；启动、工作区切换、服务管理与共享设置，Agent 接入也沿此入口 | `apps/monkeyhub/`；`OPEN_MONKEYHUB.cmd` |
| **MonkeyArch** | 三维建模、模型候选与续改 | `monkeyarch/`；Hub `appId: monkeyarch` |
| **MonkeyDiagram** | 图纸、图解、平立剖表达与单页批注 | `monkeydiagram/`；Hub rail「工具」组的「图纸」（与渲染、制作、用量并列）；Board 双击已登记图页进入精确页面编辑 |
| **MonkeyBoard** | 图版排布、方案比较、会议展示与画布批注；含「排版」模式（排版与导出，即 Publish 界面） | Hub `appId: monkeyboard`；同页项目工作区；左上角「画板 \| 排版」切换两种模式 |
| **MonkeyMonitor** | 用量、费用、耗时与计算过程查看 | `monkeymonitor/`；Hub `appId: monkeymonitor` |
| **MonkeyFab** | 制作与打印准备；当前支持分件及已切片文件发送 | 同仓 `apps/monkeyfab/` CLI；Hub `appId: monkeyfab`、`?view=fab` |

**Studio** 是项目运行时保留的服务、协议和 Python 包标识，源码在
`apps/archflow-studio/api/`。产品入口和唯一生产前端均为 MonkeyHub；建模、画板直接在
Hub 中渲染，Diagram 是画板中的图页编辑器。每个项目拥有独立运行时和客户端，
MonkeyMonitor 诊断服务仍独立运行，Usage 与 MonkeyFab 页面由 Hub 承载。

上表是产品名，不是导航层级。Hub rail 分两组（#295，#300 再定）：「工作面」是项目的主要界面，
现为建模与画板；排版是画板的模式，从画板左上角的「画板 | 排版」切换，不单列入口。状态树将作为
第三个工作面加入（#284）。「工具」作用于当前项目或本机：图纸、渲染、制作与用量。
图纸是当前设计状态的二维投影，不是与 MonkeyArch 并列的建筑权威：它在同一项目工作区、
同一 Project Runtime 与修改起点上打开，再按一次回到打开前的界面，来源与保存方式不变；渲染同样如此。

在 MonkeyBoard 中双击一页已登记图纸，会在**同一个标签页内**打开现有的图纸编辑器
（通过项目上下文传递该页的 run / asset / revision / page），批注与来源绑定沿用原有 owner；
编辑器里的 MonkeyBoard 入口先写回该页批注再返回，恢复离开时的画板视角与选中。
这条往返仅切换同一项目中的图页编辑与画板，不卸载模型或画布，也不触碰其他项目工作区
（MonkeyArch 未同步的模型草稿保持不变）。图纸能力仍归 `monkeydiagram/`，Board 不再复制一套编辑器。

设计历史统一使用以下用语，详细动作与存储约定见
[Stage / Branch / Candidate 方案](STAGE_BRANCH_CANDIDATE_PLAN.md)：

| 用语 | 含义 | 显示示例 |
| --- | --- | --- |
| **Stage** | 已接受、已提交且不可原地修改的完整设计状态节点 | `S0`、`S1`、`S2`；可附“总体建筑”“柜体调整”等说明 |
| **Branch** | 一条要持续保留和演化的历史线 | `main`、`cabinet-alt-A` |
| **Candidate** | 从确切起点生成的尚未接受提交的候选修改 | `Candidate A`、`Candidate B` |
| **Exploration** | 围绕同一起点的一次比较或决定，组织相关候选 | “柜体布局比较”；复用既有 WorkingCopy |

普通 A/B 备选称为 Candidate；一次柜体修改或多个 Agent 并行生成不自动创建 Branch。
接受候选后才得到下一 Stage；从历史 Stage 另开一条持续演化路线时才创建 Branch。
`S0/S1` 是显示编号，不替代唯一
Stage 引用，也不表示正式 issue；设计 Branch 与 Git 源码分支是不同的历史。
软件版本、项目 Stage、成果文件修订号分别命名；导出成果继续遵循第 4 节的文件名规则。

### 第 2 步：找到归口，选择扩展位置

1. 用 `python tools/devctl.py module <关键词>` 找相关 owner，再用精确 module id 查询公开 API、职责边界和源码路径，读取真实调用方。
2. 已有能力直接复用；新的独立分析、出图算法可放在自己的领域目录或外部包，通过函数、CLI、API 或 adapter 接入。
3. 只有确实新增状态语义、编译操作、持久接口或校核边界时，才扩展对应 core owner。应用功能不必逐层修改 core，也不必塞进已有大文件。
4. 新增软件归口时说明已有 owner 为什么不适合；归口、公开契约或列出的测试改变时，同一改动更新 registry。替换原型时删除被替代的生产路径，保留必要的历史数据读取。

例如朋友实现一个独立分析或出图算法：算法接收明确输入，返回分析值或图纸结果；应用层只加需要的调用，
再交给现有项目保存和展示流程。算法可以独立开发和测试，接入不需要先建一套插件平台。

[OpenAI 的公开自定义指南](https://learn.chatgpt.com/docs/customization/overview) 将长期指导、可复用 Skill 和外部 MCP 工具分开，
[Plugin 文档](https://developers.openai.com/plugins/concepts/plugins) 将插件作为安装和分发单元。这里参考其公开接口分工：
Skill 写方法、步骤和参考资料；函数或 CLI 执行具体工作，需要连接外部服务时才使用 MCP；需要团队安装分发时再打包 Plugin。
已有函数够用就不加 MCP。ArchFlow 当前没有通用插件自动发现或加载器。Studio 的 `IntentProvider` 和 `StudioEventSink` 已有应用实现与调用方；其余预留 port 不能据名称算作已接通能力。

### 第 3 步：跨接口时同步协议

OpenAI 的公开 [Codex App Server 工程文章](https://openai.com/index/unlocking-the-codex-harness/) 说明，共享协议由真实客户端需求逐步形成，并用于生成类型和固定已测后端版本。
ArchFlow 继续复用现有 FastAPI/OpenAPI 与生成客户端，外部模块从真实消费者的参数、结果契约接入，不把预留协议当作插件加载器。

- 新的对外行为先决定是否属于 protocol feature；若是，在
  `archflow_studio_api/protocol.py` 暴露 capability，并同步 `PROTOCOL.md` 的 route/status/error。
- wire shape 只写在 `api/.../transport/` 的 Pydantic DTO；业务值留在 application/kernel 的普通
  domain type。
- route 只接收调用所需身份和内容，不接收客户端指定的服务器路径。
- 有持久化时先确认现有 项目存储接口 是否足够；不够只增加最窄的 area-bound capability，并由
  `FilesystemProjectRepository` 实现。

### 第 4 步：只接本次需要的层

```text
独立领域模块或已有能力
→ 现有函数 / CLI / API / adapter
→ 应用层调用（需要接入 Studio 时）
→ 现有项目保存与展示
```

确有新的 HTTP 资源或字段时，再补 transport DTO、route 和生成客户端；确有 core 边界变化时回到对应 owner。
不在 BFF 复制 kernel 事实，不让浏览器拼项目文件路径，不手写与 Pydantic 并行的 TS 协议类型。

### 第 5 步：DTO 改动后生成客户端

在 `apps/monkeyhub/web/workspaces`：

```powershell
npm run api:generate
npm run api:check
```

`api:generate` 从真实 `create_app(...).openapi()` 生成 `src/api/generated/`。UI 再通过
`src/api/client.ts` 的现有门面调用，不直接修改 generated 文件。

### 第 6 步：以行为闭环验收

按实际触及的行为选择最小检查组合：

- owner 的聚焦单元测试；
- 写项目时，用真实临时项目 repository 测 wrong project/run、内容身份、重启读回和 `HEAD` 不变；
- API 测成功、错误码和 OpenAPI shape；
- Web 测交互，再跑 generated-client drift、typecheck 和 build；
- Python/registry 改动跑 `archcheck`；
- 最后只检查本次路径的 diff，不把共享工作树其他 WIP 算进结果。

纯文档修改只核对命令、链接、相关生成地图和 scoped diff；静态 duplicate 检查不证明算法语义没有重复，仍由测试和 review 判断。

## 8. 团队首次接入：从 GitHub 开始

完成条件：成员在自己的 clone 和 Runtime 中启动 API/Web，执行一次合成候选修改，跑过相关检查，
再把一个明确范围的源码修改交给另一人审查。首次试用不需要模型密钥、Rhino 或真实建筑材料。

### 8.1 两个仓库，一条协作路径

| GitHub 仓库 | 任务归属 | 检查入口 |
|---|---|---|
| [共享工具箱](https://github.com/cogco1/huaguoshan-digital-infrastructure) | 可复用 CLI、Skills、实验记录工具与图表/报告生成工具 | [工具箱 Actions](https://github.com/cogco1/huaguoshan-digital-infrastructure/actions)，沿用该仓库 README 和 CONTRIBUTING |
| [ArchFlow](https://github.com/cogco1/ARCHFLOW_V4) | 建模机制、MonkeyArch、候选执行和项目存储 | [ArchFlow Actions](https://github.com/cogco1/ARCHFLOW_V4/actions)，本节说明首次运行 |

两个仓库当前均为私有，默认分支都是 `main`；成员先确认能打开它们。负责人提供首个具体任务和一位
审查人。任务放在对应仓库的已有 Issue 或 PR，写清输入、预期结果、修改文件和检查方式。
改共用工具到工具箱，改建模或应用到 ArchFlow；确实涉及两边时，两个 PR 互相链接并标明依赖版本。
不在其中一个仓库复制另一个仓库的实现。

每个任务使用短分支，例如 `codex/first-setup-fix`；完成后发 PR 到 `main`，在 PR 的 Checks 看 Actions，
由另一人核对修改与运行结果，再由集成人合并。普通讨论、分工和审查都留在 GitHub，现有模块所有权仍查
`SYSTEM_MAP.md` 和 registry，不另建一份模块表。成员各自运行，真实项目数据和密钥留在约定的项目目录。

### 8.2 获取代码并在本机运行

维护者提供仓库地址、访问权限和此次试用的源码提交。每人独立 clone；本机只保留 `main` 的约定
针对维护者的主检出目录，不要求队员共用它。以下两处路径由成员自己选择，Runtime 必须在源码仓外：

```powershell
$SourceRoot = 'D:\code\ARCHFLOW_V4'
$RuntimeRoot = 'D:\ArchFlowRuntime\first-trial'
git clone --branch main https://github.com/cogco1/ARCHFLOW_V4.git $SourceRoot
Set-Location $SourceRoot
git rev-parse HEAD
git status --short
```

共享工具箱同样独立 clone 到另一个源码目录，再按它的 README 安装；不嵌入 ArchFlow，也不共用 Python venv。

上述命令取得远端当前 `main`。如果维护者明确提供了其他分支、tag 或提交，使用那份已发布的源码：

```powershell
$SourceRef = '<维护者提供的源码 ref>'
git fetch origin $SourceRef
git switch --detach FETCH_HEAD
git rev-parse HEAD
```

确认取得的提交包含本节所用的创建命令和配置模板，再进行安装。后续开发从约定基线开短分支；
8.8 记录旧版交付事实，不作为默认安装版本。

需要已安装 Git、Python 3.12 和 Node.js 24；9 月 5 日首次隔离核验版本为 Python 3.12.10 / Node.js 24.14.0。
Vite 声明的 Node 下限为 `^20.19.0 || >=22.12.0`，这里选 Node 24 同时覆盖直接运行 TypeScript 的 Web 测试。

```powershell
py -3.12 --version
node --version
py -3.12 -m venv "$RuntimeRoot\venv"
$Python = "$RuntimeRoot\venv\Scripts\python.exe"
$env:PATH = "$RuntimeRoot\venv\Scripts;" + $env:PATH
& $Python -m pip install -e '.[cad-inspection]'
& $Python -m pip install -r apps/archflow-studio/api/requirements.txt -r apps/monkeyhub/api/requirements.txt httpx2
& $Python -m pip check
npm.cmd ci --prefix apps/monkeyhub/web
npm.cmd ci --prefix apps/monkeyhub/web/workspaces/tools/openapi-ts
python -c "import sys; print(sys.executable)"
```

最后一行必须指向刚创建的 venv。无需修改系统执行策略或全局安装包；后续 Python 命令使用
`$Python` 或上述 PATH 中的 `python`。带版本的 `py -3.12` 会选系统解释器，不能用于 venv 内检查。
两份 npm lockfile 已固定 Web 和 OpenAPI 生成器依赖；Python API 目前使用版本范围，尚无完整锁文件。
`api:dump` 使用 PATH 中的 `python`，因此生成 SDK 和 API 检查共用这个 venv。

### 8.3 创建外部项目

仓库不随 clone 分发正式模型项目。使用生产命令
[`tools/create_project.py`](../tools/create_project.py) 创建自己的 P036 项目；该命令不导入测试夹具。
先选择源码仓外的目标目录，目录名就是项目 ID：

```powershell
Set-Location $SourceRoot
$ProjectDir = Join-Path $RuntimeRoot 'workspace\projects\my-project'
```

只需建立连接和检查项目状态时，创建空项目：

```powershell
& $Python tools/create_project.py --project $ProjectDir
```

命令建立版本 0 和空的 `StateRecord@1`，不创建 run、模型或分工。它可以绑定 Studio 并查看空状态；
Program 和建模候选需要相应的完整设计输入与执行分工，PDF 保存还需要一个实际存在的 run。
空视口或连接成功不代表这些功能已经有可运行的输入。

已有自己编写或获准使用的设计输入时，**以这条命令代替上面的空项目创建**，将示例输入路径换成自己的：

```powershell
& $Python tools/create_project.py --project $ProjectDir `
    --state-record 'D:\design-inputs\state-record.json' `
    --seats-file 'D:\design-inputs\seats.json'
```

`state-record.json` 必须是未绑定历史版本的 `StateRecord@1`：`project_id` 与目录名一致，`base` 为 `null` 或省略；
分工文件沿用现有 runner seat pack 格式。输入可为 UTF-8 或带 BOM 的 UTF-8，命令通过现有解析器校验后，
由 P036 写入 `input/runner/`。只提供状态记录也能初始化，但在运行建模候选前仍需完整分工。
这一步不自动补建筑关系、生成模型或建立历史运行记录。

目标必须不存在或为空目录；已有项目不会被覆盖。以上两种创建方式只选一种，之后继续使用同一项目。
若要接着既有 run 工作，使用负责人提供的完整 P036 项目副本，不用其已绑定的单份记录重新初始化。

项目要换机器、换用户或留一份离线备份时，用项目归档，不要手工复制目录：一个归档就是一份
`ProjectArchiveManifest@1` 加它点名的保留字节，导出与还原在 MonkeyHub 的项目卡片上各有一个对话框，
命令行是 `tools/create_project.py --export-archive/--restore-archive`；凭据、进程与运行时状态、缓存、
可重建预览和无界日志不随归档走，还原出来的项目也不需要源机器的 Hub 运行时目录、聊天或配置。
搬家是否真的成功，用 `scripts/dev/run-archive-rehearsal.ps1` 完整演练一遍：导出、还原到空目录、
在还原副本上启动一个项目运行时、按正常读取器逐项比对身份，并从还原后的精确 base 跑一个不接受的候选。
两个对话框、两条路由、命令行开关、演练脚本与驱动，以及“什么会走、什么不会走”的完整说明，见
[Hub 说明的 “Project archive: export, restore, rehearsal”](../apps/monkeyhub/README.md#project-archive-export-restore-rehearsal)。

### 8.4 启动前后端

#### 通过 MonkeyHub 启动

生产入口只有 MonkeyHub：安装包的桌面窗口，或源码根目录的 `OPEN_MONKEYHUB.cmd`。源码开发用
`apps/monkeyhub/launch-hub.ps1`（见 [Hub 说明](../apps/monkeyhub/README.md)）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SourceRoot\apps\monkeyhub\launch-hub.ps1" -Python $Python -RuntimeRoot "$RuntimeRoot\hub" -HubWebDir "$SourceRoot\apps\monkeyhub\web\dist"
```

唯一的前端 `dist` 目录来自 `npm --prefix apps/monkeyhub/web run build`。Hub 启动后在其设置里选择 8.3 创建的项目、CAD 后端
（默认 `occt`，需要 `.[cad-occt]`；未安装时选 `off`）和参考 run；意图 provider、模型与超时来自 Hub 的
用户偏好。每个项目的 Studio 进程由 Hub 创建、监控和关闭；退出走托盘的 `Quit MonkeyHub`。
不要与下面的手动方式同时使用同一组端口。

真实模型首轮试用前，项目负责人需提供允许共享的完整项目副本，包含设计输入、选定 run 的记录及其引用的
模型文件，并说明源码版本、run 和材料使用范围；项目路径改为这份副本的路径，参考 run
填选定的 run。单独一份 3DM 不等于可继续修改的完整项目，合成项目的空视口也不作为建筑功能验收。

#### 开发时直接启动项目运行时

API smoke 与隔离的工作区浏览器回归可以直接启动显式项目的 API-only 运行时：

```powershell
$env:ARCHFLOW_STUDIO_CAD_EXPORT = 'off'
$env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'deterministic'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SourceRoot\scripts\dev\run-project-runtime.ps1" -ProjectDir $ProjectDir -Python $Python -Port 18080
```

这个脚本只做一件事：要求显式 `-ProjectDir`，把源码根与 `apps/archflow-studio/api` 放进 `PYTHONPATH`，
前台运行 `archflow_studio_api.main`，Ctrl+C 结束。它没有配置文件、没有默认项目、没有启动窗口和托盘，
也不管理任何生命周期；其余设置全部是 API 本来就读取的 `ARCHFLOW_STUDIO_*` 环境变量。
“可独立运行”不等于“独立产品入口”：生产环境里项目运行时的生命周期只属于 MonkeyHub。工作区测试页位于
`apps/monkeyhub/web/workspaces/test/`，不进入生产构建；实际产品交互通过 Hub 验收。

#### 手动联调

终端 A：

```powershell
Set-Location "$SourceRoot\apps\archflow-studio\api"
$env:ARCHFLOW_STUDIO_PROJECT_DIR = $ProjectDir
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = ''
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'deterministic'
$env:ARCHFLOW_STUDIO_RHINO_EXPORT = '0'
& $Python -m archflow_studio_api.main --host 127.0.0.1 --port 18080
```

终端 B，重新设置自己选择的 `$SourceRoot`：

```powershell
$SourceRoot = 'D:\code\ARCHFLOW_V4'
Set-Location "$SourceRoot\apps\monkeyhub\web"
$env:ARCHFLOW_STUDIO_API_URL = 'http://127.0.0.1:18080'
npx.cmd vite --config workspaces/test/vite.config.ts --port 15174
```

打开测试页 `http://127.0.0.1:15174/test/workspace.html`（仅隔离回归，不进入生产构建）。这组端口与默认的一键启动端口分开；端口被占用时一起改 API 端口和
代理地址，不停止别人的服务。新建空项目没有导出模型，空视口是预期结果；通过上述建模准备入口后可创建首个候选。
完成后在两个终端分别按 Ctrl+C。

首次路径全部使用进程环境变量。密钥不写源码、配置文件或 PR；以后选用模型 provider 时由成员
按 Studio 指南配置自己的凭据。真实项目必须由其负责人明确提供可共享的项目副本与选定 run，
不复制维护者的整个 Runtime。运行候选会写入绑定项目的 runs，试用始终绑定自己的副本。

### 8.5 在有设计输入的项目中完成一次候选修改

本节用于已提供完整设计输入和分工、且所选元素支持数值修改的项目。只有空记录时，先完成 8.3 的输入准备。
终端 C 通过 Web 的代理读取当前状态，查看真实的构件、元素与数值字段：

```powershell
$api = 'http://127.0.0.1:15174/api'
Invoke-RestMethod "$api/health"     # projectBound 应为 true
Invoke-RestMethod "$api/protocol"   # archflow/2
$state = Invoke-RestMethod "$api/state"
$state.elements | Select-Object componentId, elementId, numericFields | Format-List
```

从返回内容选择实际元素及其已有数值字段，目标值由设计者决定：

```powershell
$elementId = Read-Host '输入上面列出的 elementId'
$element = $state.elements | Where-Object elementId -eq $elementId | Select-Object -First 1
if (-not $element) { throw '当前状态中没有这个元素。' }
$field = Read-Host '输入该元素 numericFields 中的字段名'
if ($element.numericFields.PSObject.Properties.Name -notcontains $field) { throw '这个元素没有该数值字段。' }
$value = Read-Host '输入目标数值，小数点使用 .'
$body = @{
    stateDigest = $state.stateDigest
    targetComponentId = $element.componentId
    elementId = $element.elementId
    utterance = "set $field to $value"
} | ConvertTo-Json
$proposal = Invoke-RestMethod "$api/proposals" -Method Post -ContentType 'application/json' -Body $body
$accepted = Invoke-RestMethod "$api/proposals/$($proposal.proposalId)/candidate" -Method Post
Invoke-RestMethod "$api/jobs/$($accepted.jobId)"
```

若最后仍是 `queued/running`，稍后重读该 job；不重复提交 candidate。`succeeded` 后读取：

```powershell
Invoke-RestMethod "$api/candidates/$($accepted.candidateId)"
$result = Invoke-RestMethod "$api/state?run=$($accepted.candidateId)"
($result.elements | Where-Object elementId -eq $element.elementId).numericFields.$field
```

检查返回字段是否等于所填目标值。被锁定或由表达式驱动的字段可能需要修改其上游参数，按 API 的具体返回处理。
这一步调用现有执行器并保留候选记录；关闭导出时 `artifacts` 为空，不代表生成了模型。
项目 `HEAD` 保持原值，下一次查看默认状态也不会自动变成刚做的候选。

### 8.6 GitHub Actions 与本机相关检查

既有 [verify.yml](../.github/workflows/verify.yml) 包含架构、PR 提交范围、内核/API、Web 和 Ubuntu/Windows 首次接入检查。
每次交付查看对应提交或 PR 的实际 Checks；8.8 中的历史检查不代表当前提交已经通过。
`archcheck` 的当前静态边界检查与 `--changed` 的历史提交范围检查分别执行，不能互相替代。

在另一个终端重新设置 `$SourceRoot`、`$RuntimeRoot`、`$Python` 与 venv PATH，按上述路径运行：

```powershell
Set-Location $SourceRoot
& $Python tools/archcheck.py
Set-Location "$SourceRoot\apps\archflow-studio\api"
& $Python -m unittest tests.test_health tests.test_protocol tests.test_candidate
Set-Location "$SourceRoot\apps\monkeyhub\web"
npm.cmd test
npm.cmd run api:check
npm.cmd run build  # 包含 typecheck
```

之后按真实改动选择检查：纯文档查命令、链接和 diff；内核改动跑 registry 所列的受影响测试与
`archcheck`；API/DTO 改动检查相应路由与 `api:check`，Web 改动检查交互与 build。
这套首次接入检查不覆盖所有功能，任务涉及其他模块时仍要补跑该模块相关检查。
只有 registry 或语义表真的改变时，才用 `python tools/devctl.py render-map` 更新生成地图。

### 8.7 从一个小修改到审查与集成

1. 先按第 2 节找到现有 owner，查看它的 inputs/outputs/public_api/invariants 和真实调用方。
   在对应的 GitHub Issue（没有就新开）或 PR 中约定问题、明确文件范围、接口是否改变、验收动作和审查人；
   需要源码并发协调时才在 work registry 登记 `GH-<issue>` lane，不新建 P 卡。
2. 成员在自己的 clone 从约定基线建立短分支，如 `git switch -c codex/first-setup-fix`。
   首次源码修改选一个已经复现的小问题；与其他人重叠同一文件时先交接范围再编辑。
3. 检查工作 diff，显式暂存自己的文件。例如只修改 README 时：

   ```powershell
   git diff -- README.md
   git add -- README.md
   git diff --cached --name-only
   git diff --cached
   git commit -m "P000-governance: clarify first-run setup"
   ```

4. 把分支/提交交给约定审查人；已获仓库写权限的成员按团队约定提交 PR。PR 写触发问题、修改后行为、
   基线、实际检查和影响使用的限制。不能将进程环境、生成文件或他人的 WIP 收进提交。
5. 审查人核对准确 diff 和受影响接口，在自己的环境重跑相关检查。集成人只合入已审查提交，
   冲突在该短分支解决后复核；源码合并不改变任何项目的 `HEAD`。维护者主检出按已有明确约定保留，
   不在有他人 WIP 的目录切换成员分支。同步基线按任务需要进行，不要求每日 rebase。

首次交接完成的标准是另一位成员确实拿到相同版本、复跑并审查了一次修改；本机自测不能代签。

### 8.8 历史核验记录

以下是 2026-09-05 的历史核验记录：在外部 Runtime 的 `temp/team-onboarding-20260905/` 中，用当时本地 `main`
（`4a4e196e9a64ac50a4b9f4e23611e1af35888359`）的独立 clone 和新 venv
完成依赖安装、API/Web 启动、通过 Web 代理的 health/protocol/state 请求及 `portico-base.height = 2.2`
候选执行。候选重新读取值为 2.2，`HEAD` 前后相同，未生成 CAD artifact。
Python 实装为 FastAPI 0.141.1、uvicorn 0.52.4、Pydantic 2.13.5、Pillow 12.3.0、httpx2 2.12.0、rhino3dm 8.32.1；
API 聚焦检查 58 项、其中 1 项真实 villa 输入检查跳过，Web 12 项通过且构建成功。
该 clone 另外应用了当日两处脚本修正：OpenAPI 使用 venv Python，生成客户端比较忽略 CRLF/LF 差别。
Windows 换行的 `api:check` 已通过；接口正文差异仍会报错。`archcheck` 通过。
这些结果属于当日的本机隔离验证；当时第二位成员、浏览器交互和真实模型试用尚未验证。

同日 GitHub `main` 实查为 `7b3d09f`，本地核验基线比它多 10 笔提交；当日五文件修改只在本地交付，
新增 Actions 尚未在 GitHub 运行。这些历史结果不作为当前版本状态。

2026-09-08 的分发候选为 [PR #3](https://github.com/cogco1/ARCHFLOW_V4/pull/3) 的
`217204171f1ac69088fb54977acc124dc1284c2e`，四个 CI job 已通过，默认远端 main 仍为 `7b3d09f`。
该候选包含已提交的 Studio 基线和历史 scope 修复，不包含随后开发的文档视觉输入或主检出其他未提交修改。
以上是该候选当时的状态，不作为默认安装版本；当时第二位成员的独立复现仍待完成，维护者检查与 CI 不替代成员试用。

首位队友试用前，负责人还需提供：两个 GitHub 仓库的成员访问权限、此次分发版本、首位成员与审查人、
一个小修改的文件范围。空项目连接检查不依赖建筑模型；试用真实建筑或候选修改时，再提供允许共享的完整输入或项目副本。

## 9. 与共享工具箱的分工

花果山独立仓库 `D:\huaguoshan-digital-infrastructure` 已有研究初始化、实验运行记录、Atlas、图表、报告、
论文候选骨架和成员证据入口；协作约定见该仓库 `CONTRIBUTING.md`、`RESEARCH_WORKFLOW.md`。
这些能力不在 ArchFlow 重写。ArchFlow 保留建模、状态、候选执行和项目事实；ResearchOps 的真实
thread 放其独立研究工作区，通过明确源码版本与工件引用联系两边。

ResearchOps README 当前明确 ArchFlow/MonkeyArch 执行适配尚未接入，已有合成演示不代表真实科研链已跑通。
先由第一位成员完成上述开发接入，再由一个真实课题决定需要怎样调用现有执行接口；只有实际重复使用的
实验步骤才提取共用能力。研究问题、实验解释、claim 与署名仍由研究者判断。
