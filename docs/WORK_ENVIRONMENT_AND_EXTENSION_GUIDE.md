# ArchFlow 团队接入与开发环境

**用途：** 给在 ArchFlow V4 上开发、联调和保存项目结果的人一条可执行的最短路径。  

**队友从第 8 节开始：GitHub 领任务 → 独立运行 → 小修改 → PR → Actions → 他人审查 → 合并。**
第 1–7 节供涉及内核和项目存储的开发查阅，首次启动不需要先读完。

**规则分工：** `AGENTS.md` 保留少量长期规则，`CONTRIBUTING.md` 说明实际协作流程；
module registry 管软件归口与公开契约，work registry 只管未完成任务和源码范围，policy 管可执行静态检查。
本文是索引和操作指南。发生冲突时，软件归口以
[`governance/module_registry.json`](../governance/module_registry.json) 为准，对外协议以
[`PROTOCOL.md`](PROTOCOL.md) 为准，项目落盘以
[`archflow/project/layout.py`](../archflow/project/layout.py) 与
[`archflow/project/repository.py`](../archflow/project/repository.py) 为准。

下文的实现说明以当前代码和 owner 为依据；本机启动核验与第二位成员实际试用分别报告。

## 1. 先建立正确的三层物理边界

### 1.1 源码仓不是项目数据盘

```text
Git 源码仓 / worktree
  archflow/                  公共项目底座、建筑事实和技术接口
  monkeyarch/                三维建模算法与运行编排
  monkeydiagram/             图纸投影与表达
  apps/archflow-studio/      共同宿主；面向两个平行工作流
  governance/                owner、依赖与架构防火墙
  docs/                      人读文档，不是实时项目状态
  tests/                     测试代码；运行时只产生可丢弃的临时输出
  probes/                    经明确审查后提交的小型机制证据

外部运行根（不进 Git；由调用方按用途显式提供）
  <workspace_root>/projects/<project_id>/   活跃项目
  <cache_root>/                             可重建缓存
  <temp_root>/                              可删除临时数据
```

**已验证：** 活跃项目与 `probes/` 使用同一种项目目录格式；它们只因是否被明确提升进
Git 而不同，不得为外部项目再建第二个数据库或写入器。通用说明见
[`archflow/project/README.md`](../archflow/project/README.md)，外部根的配置形状见
Studio 启动器读取的 [`apps/archflow-studio/runtime.json`](../apps/archflow-studio/runtime.json)
只配置该应用的 `project_dir` 等设置，不统一配置 workspace/cache/temp；该文件目前仍跟踪本机绝对路径，
新人使用第 8 节的显式环境变量，不照抄这份配置。

缓存和临时根不是项目记录。能被删除而不改变设计含义的内容才可以进入那里；证据、模型、
验收回执和恢复所需数据不能借 `temp` 绕过项目存储。

长期源码按 **ArchFlow 公共底座、MonkeyArch 建模业务、MonkeyDiagram 图纸业务** 分清目录与依赖。
当前代码所在位置与目标归属的具体映射只维护在 [REPO_LAYOUT.md](REPO_LAYOUT.md)；
迁移随真实调用链进行，不在安装时复制三套代码，也不让每位成员自行决定目录结构。

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

## 2. 先读地图，再读目录树

新增或修改功能前，按这个顺序读取：

1. [`AGENTS.md`](../AGENTS.md)：项目级硬边界。
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)：现有职责、已确认缺口和开发顺序；拟议能力不当作已实现。
3. [`SYSTEM_MAP.md`](SYSTEM_MAP.md) 中与本次行为相关的条目及
   [`module_registry.json`](../governance/module_registry.json) 中目标 capability 的 owner。
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
| Web 同步和构建产物 | `apps/archflow-studio/web/.generated/`、`dist/` | 可重建 |

**旧规则的分层修正。**
工作区的 [`GENERATION_RECORD_SPEC.md`](<file:///D:/PROJECTS/01_ACTIVE_当前项目/ARCHFLOW CAADRIA 2027/V4_RUNTIME/GENERATION_RECORD_SPEC.md>)
（`D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME\GENERATION_RECORD_SPEC.md`，已随论文材料移出仓库）第八节原本把“预览模型、截图、审查包”统一
路由到 `V4_RUNTIME/output/`。那是 2026-08-30 的跨项目交付/过手规则，粒度不足以表达后来增加的
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

## 5. Studio C/S：一条 API 边界，三层权责

```text
React / Vite / three.js / rhino3dm-wasm
  apps/archflow-studio/web
                │ OpenAPI-generated SDK
                ▼
FastAPI BFF
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

### 浏览器 `web/`

- 负责交互、视口、显示状态和本机 UI 偏好。
- 通过 `web/src/api/generated/` 的 SDK 调 API；不得手写一套平行 DTO。
- 不导入 Python kernel，不选择项目文件路径，不做 canonical 写入。
- 浏览器推导的 mesh、选中状态或截图 Blob 都不是项目记录；需要保留时交给 API。

本地用户默认值通过 `GET/PUT /api/settings/user` 保存语言、主题、字号、intent provider、model 和 timeout；
`developerMode` 是额外的浏览器本地偏好。`GET /api/settings` 读取进程运行设置，不是用户默认值的保存入口。

### 本地 FastAPI BFF `api/`

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

### 第 2 步：找到归口，选择扩展位置

1. 在 `SYSTEM_MAP.md` 和 module registry 找相关 owner，读取它的公开 API、职责边界和真实调用方。
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

在 `apps/archflow-studio/web`：

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
git clone https://github.com/cogco1/ARCHFLOW_V4.git $SourceRoot
Set-Location $SourceRoot
git fetch origin refs/pull/3/head
git switch --detach 217204171f1ac69088fb54977acc124dc1284c2e
git rev-parse HEAD
git status --short
```

共享工具箱同样独立 clone 到另一个源码目录，再按它的 README 安装；不嵌入 ArchFlow，也不共用 Python venv。

上述命令用于自己的首次 clone，取 [PR #3](https://github.com/cogco1/ARCHFLOW_V4/pull/3) 的本轮候选 `2172041`。
2026-09-08 核对时默认远端 `main` 仍为 `7b3d09f`，直接 clone 后停在 main 不会得到该候选；后续开发从约定基线开短分支。历史核验与本轮候选状态见 8.8。

需要已安装 Git、Python 3.12 和 Node.js 24；9 月 5 日首次隔离核验版本为 Python 3.12.10 / Node.js 24.14.0。
Vite 声明的 Node 下限为 `^20.19.0 || >=22.12.0`，这里选 Node 24 同时覆盖直接运行 TypeScript 的 Web 测试。

```powershell
py -3.12 --version
node --version
py -3.12 -m venv "$RuntimeRoot\venv"
$Python = "$RuntimeRoot\venv\Scripts\python.exe"
$env:PATH = "$RuntimeRoot\venv\Scripts;" + $env:PATH
& $Python -m pip install -e '.[cad-inspection]'
& $Python -m pip install -r apps/archflow-studio/api/requirements.txt httpx2
& $Python -m pip check
npm.cmd ci --prefix apps/archflow-studio/web
npm.cmd ci --prefix apps/archflow-studio/web/tools/openapi-ts
python -c "import sys; print(sys.executable)"
```

最后一行必须指向刚创建的 venv。无需修改系统执行策略或全局安装包；后续 Python 命令使用
`$Python` 或上述 PATH 中的 `python`。带版本的 `py -3.12` 会选系统解释器，不能用于 venv 内检查。
两份 npm lockfile 已固定 Web 和 OpenAPI 生成器依赖；Python API 目前使用版本范围，尚无完整锁文件。
`api:dump` 使用 PATH 中的 `python`，因此生成 SDK 和 API 检查共用这个 venv。

### 8.3 创建可共享的合成试用项目

仓库当前没有随 clone 分发的正式模型项目。复用
[`make_empty_project`](../apps/archflow-studio/api/tests/support.py) 创建小型项目测试夹具：
它通过既有项目存储接口写入设计输入和执行分工，不伪造历史执行记录。
这是开发试用数据；不代表真实设计成果，也不生成 3DM。

```powershell
Set-Location "$SourceRoot\apps\archflow-studio\api"
$env:ARCHFLOW_ONBOARDING_PROJECTS = "$RuntimeRoot\workspace\projects"
& $Python -c "import os; from pathlib import Path; from tests.support import make_empty_project; print(make_empty_project(Path(os.environ['ARCHFLOW_ONBOARDING_PROJECTS'])).layout.root)"
```

结果是 `$RuntimeRoot\workspace\projects\demo-project`。只初始化一次；再次试用可以继续使用该项目，
要从头开始则换一个 Runtime 目录。测试夹具只在接入命令和测试中使用，不由生产 API 导入。

### 8.4 启动前后端

#### 使用自己的启动配置

首次安装按 8.2 完成。之后可复用现有启动器的 `-RuntimeConfig` 参数：配置保存在自己的 Runtime，
不修改仓库中带本机路径的 `apps/archflow-studio/runtime.json`。目前没有另一个便携 sample，以下直接使用
现有配置格式；在已设置 `$SourceRoot`、`$RuntimeRoot`、`$Python` 的 PowerShell 中执行一次：

```powershell
$RuntimeConfig = Join-Path $RuntimeRoot 'config\studio.json'
if (Test-Path -LiteralPath $RuntimeConfig) { throw '配置已存在，请编辑自己的配置或另选文件名。' }
New-Item -ItemType Directory -Force -Path (Split-Path $RuntimeConfig) | Out-Null
@{
    schema_version = 'archflow-studio-runtime@1'
    project_dir = "$RuntimeRoot\workspace\projects\demo-project"
    reference_run = ''
    python = $Python
    intent_provider = 'deterministic'
    rhino_export = $false
    api_port = 18080
    web_port = 15174
    open_browser = $true
} | ConvertTo-Json | Set-Content -LiteralPath $RuntimeConfig -Encoding UTF8
```

`python` 填虚拟环境中 `python.exe` 的完整路径，路径含空格也可直接写入；若需要附加简单参数，
写成 `"完整路径\python.exe" -I`。原来的 `py -3.12` 与省略该字段时的默认行为保留，
但这两种方式选择系统 Python，不会自动选择自己的 venv。

以后从同一组路径启动：

```powershell
$config = Get-Content -LiteralPath $RuntimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_API_URL = "http://127.0.0.1:$($config.api_port)"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "$SourceRoot\apps\archflow-studio\launch-studio.ps1" -RuntimeConfig $RuntimeConfig
```

前端代理地址需与配置的 `api_port` 一致；当前启动器不会替自定义端口设置该环境变量。
使用 Windows PowerShell 5.1 启动；`-ExecutionPolicy Bypass` 仅作用于这次进程。
需要暂不打开浏览器时加 `-NoBrowser`，它仍会启动应用窗口和两个服务。结束时通过托盘的
`Quit MonkeyArch` 关闭这次启动的服务。不要与下面手动启动方式同时运行同一组端口。

真实模型首轮试用前，项目负责人需提供允许共享的完整项目副本，包含设计输入、选定 run 的记录及其引用的
模型文件，并说明源码版本、run 和材料使用范围；`project_dir` 改为这份副本的路径，`reference_run`
填选定的 run。单独一份 3DM 不等于可继续修改的完整项目，合成项目的空视口也不作为建筑功能验收。

本轮已用临时项目和真实临时 venv 验证外部配置读取、含空格/带引号的 Python 路径、参数传递和
Python 子进程启动；保留了 `py -3.12` 的兼容检查。测试没有运行启动器窗口、前后端服务或浏览器。
新队友完整启动、真实模型与远端环境仍需实际试用。

#### 手动联调

终端 A：

```powershell
Set-Location "$SourceRoot\apps\archflow-studio\api"
$env:ARCHFLOW_STUDIO_PROJECT_DIR = "$RuntimeRoot\workspace\projects\demo-project"
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = ''
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'deterministic'
$env:ARCHFLOW_STUDIO_RHINO_EXPORT = '0'
& $Python -m archflow_studio_api.main --host 127.0.0.1 --port 18080
```

终端 B，重新设置自己选择的 `$SourceRoot`：

```powershell
$SourceRoot = 'D:\code\ARCHFLOW_V4'
Set-Location "$SourceRoot\apps\archflow-studio\web"
$env:ARCHFLOW_STUDIO_API_URL = 'http://127.0.0.1:18080'
npm.cmd run dev -- --port 15174
```

打开 `http://127.0.0.1:15174`。这组端口与默认的一键启动端口分开；端口被占用时一起改 API 端口和
代理地址，不停止别人的服务。合成项目没有导出模型，空视口是预期结果；状态与候选链可用。
完成后在两个终端分别按 Ctrl+C。

首次路径全部使用进程环境变量。密钥不写源码、runtime.json 或 PR；以后选用模型 provider 时由成员
按 Studio 指南配置自己的凭据。真实项目必须由其负责人明确提供可共享的项目副本与选定 run，
不复制维护者的整个 Runtime。运行候选会写入绑定项目的 runs，试用始终绑定自己的副本。

### 8.5 完成一次候选修改

终端 C，通过 Web 的代理检查整个 HTTP 通路：

```powershell
$api = 'http://127.0.0.1:15174/api'
Invoke-RestMethod "$api/health"     # projectBound 应为 true
Invoke-RestMethod "$api/protocol"   # archflow/2
$state = Invoke-RestMethod "$api/state"
$body = @{
    stateDigest = $state.stateDigest
    targetComponentId = 'portico'
    elementId = 'portico-base'
    utterance = 'set height to 2.2'
} | ConvertTo-Json
$proposal = Invoke-RestMethod "$api/proposals" -Method Post -ContentType 'application/json' -Body $body
$accepted = Invoke-RestMethod "$api/proposals/$($proposal.proposalId)/candidate" -Method Post
Invoke-RestMethod "$api/jobs/$($accepted.jobId)"
```

若最后仍是 `queued/running`，稍后重读该 job；不重复提交 candidate。`succeeded` 后读取：

```powershell
Invoke-RestMethod "$api/candidates/$($accepted.candidateId)"
$result = Invoke-RestMethod "$api/state?run=$($accepted.candidateId)"
($result.elements | Where-Object elementId -eq 'portico-base').numericFields.height  # 2.2
```

这一步调用现有执行器并保留候选记录；关闭导出时 `artifacts` 为空，不宣称 Rhino 执行成功。
项目 `HEAD` 保持原值，下一次查看默认状态也不会自动变成刚做的候选。

### 8.6 GitHub Actions 与本机相关检查

既有 [verify.yml](../.github/workflows/verify.yml) 包含架构、PR 提交范围、内核/API、Web 和 Ubuntu/Windows 首次接入检查。
本轮候选 `2172041` 在 [PR #3 Checks](https://github.com/cogco1/ARCHFLOW_V4/pull/3/checks) 的四个 CI job 均已通过；
9 月 5 日“workflow 尚未在远端运行”的记录属于当时状态。后续提交仍以其实际 Checks 为准。
`archcheck` 的当前静态边界检查与 `--changed` 的历史提交范围检查分别执行，不能互相替代。

在另一个终端重新设置 `$SourceRoot`、`$RuntimeRoot`、`$Python` 与 venv PATH，按上述路径运行：

```powershell
Set-Location $SourceRoot
& $Python tools/archcheck.py
Set-Location "$SourceRoot\apps\archflow-studio\api"
& $Python -m unittest tests.test_health tests.test_protocol tests.test_candidate
Set-Location "$SourceRoot\apps\archflow-studio\web"
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
   复用已有 live 工作卡，在任务或 PR 中约定问题、明确文件范围、接口是否改变、验收动作和审查人；无合适归属时才新建卡。
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

### 8.8 历史核验与本轮候选

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
成员可按 8.2 取得准确提交；第二位成员的独立复现仍待完成，维护者检查与 CI 不替代成员试用。

首位队友试用前，负责人还需提供：两个 GitHub 仓库的成员访问权限、此次分发版本、首位成员与审查人、
一个小修改的文件范围。若要看真实建筑，再提供允许共享的项目副本；合成接入不依赖它。

## 9. 与共享工具箱的分工

花果山独立仓库 `D:\huaguoshan-digital-infrastructure` 已有研究初始化、实验运行记录、Atlas、图表、报告、
论文候选骨架和成员证据入口；协作约定见该仓库 `CONTRIBUTING.md`、`RESEARCH_WORKFLOW.md`。
这些能力不在 ArchFlow 重写。ArchFlow 保留建模、状态、候选执行和项目事实；ResearchOps 的真实
thread 放其独立研究工作区，通过明确源码版本与工件引用联系两边。

ResearchOps README 当前明确 ArchFlow/MonkeyArch 执行适配尚未接入，已有合成演示不代表真实科研链已跑通。
先由第一位成员完成上述开发接入，再由一个真实课题决定需要怎样调用现有执行接口；只有实际重复使用的
实验步骤才提取共用能力。研究问题、实验解释、claim 与署名仍由研究者判断。
