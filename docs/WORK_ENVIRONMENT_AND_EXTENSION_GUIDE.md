# ArchFlow 工作环境与架构搭建方法论

**用途：** 给在 ArchFlow V4 上开发、联调和保存项目结果的人一条可执行的最短路径。  
**审计基线：** 2026-09-04，`D:\ARCHFLOW_V4` 的 `main`（`1e4d9cae`）及当时未提交的 Studio 截图工作树。  
**权威边界：** 本文是索引和操作指南，不另建架构。发生冲突时，代码所有权以
[`governance/module_registry.json`](../governance/module_registry.json) 为准，对外协议以
[`PROTOCOL.md`](PROTOCOL.md) 为准，项目落盘以
[`archflow/project/layout.py`](../archflow/project/layout.py) 与
[`archflow/project/repository.py`](../archflow/project/repository.py) 为准。

本文采用三个状态标签：

- **已验证**：当前主干文件和已登记 owner 能共同证明的行为。
- **工作树 WIP**：在当前工作树有实现和测试，但尚未进入上述审计基线提交。
- **规划/历史**：计划、迁移说明或旧规则，不可当作当前接口直接调用。

## 1. 先建立正确的三层物理边界

### 1.1 源码仓不是项目数据盘

```text
Git 源码仓 / worktree
  archflow/                  可复用内核与 P036 机制
  apps/archflow-studio/      Studio 产品壳
  governance/                owner、依赖与架构防火墙
  docs/                      人读文档，不是实时项目状态
  tests/                     测试代码；运行时只产生可丢弃的临时输出
  probes/                    经明确审查后提交的小型机制证据

外部运行根（不进 Git；三个根分别由配置显式给出）
  <workspace_root>/projects/<project_id>/   活跃 P036 项目
  <cache_root>/                             可重建缓存
  <temp_root>/                              可删除临时数据
```

**已验证：** 活跃项目与 `probes/` 使用同一种 P036 envelope；它们只因是否被明确提升进
Git 而不同，不得为外部项目再建第二个数据库或写入器。通用说明见
[`archflow/project/README.md`](../archflow/project/README.md)，外部根的配置形状见
Studio 启动器读取的 [`apps/archflow-studio/runtime.json`](../apps/archflow-studio/runtime.json)
（`config/runtime.example.json` 已随退役命令归档到外部批次 `20260905_repo_cloud-config-reports-plans`）。

缓存和临时根不是项目记录。能被删除而不改变设计含义的内容才可以进入那里；证据、模型、
验收回执和恢复所需数据不能借 `temp` 逃离 P036。

### 1.2 `main`、Git worktree 和项目 `HEAD` 是三件事

- Git 的 `main` 是**源码版本线**。
- Git worktree 是同一 Git 仓库历史上的一个独立检出目录。不同 worktree 的未提交文件互不
  自动出现；要进入 `main`，仍需明确提交并合并或摘取。
- 同一物理检出目录中的多个代理会立即看到彼此的未提交文件，也会互相污染暂存区。
- 项目根内的 `HEAD` 是 P036 的**已发布设计位置**，不是 Git 分支，也不随源码合并移动。

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

默认决策是 **EXTEND 现有 owner**。只有 registry 中没有能承担该职责的 owner，且能写明原因，
才 CREATE；不能让“先做个能跑的”变成第二套状态、路径、协议或编译器。

## 3. P036 项目 envelope：每个东西只有一个家

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
| `objects/sha256/` | 经 P036 导入的不可变二进制内容 | 任意文件仓或浏览器下载目录 |
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
| 外部原始证据或请求 | 项目 `input/`；需要不可变二进制身份时由 P036 `ingest` 进入 `objects/sha256/` | 非 canonical 输入 |
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
project-bound Studio capture；该表现已按当前 P036、protocol、registry 和 API 工作树实现拆分为：

- 能明确归属于一个已有 run 的过程截图，进入该 run 的 `workspaces/studio-captures/`；
- 从一个项目正式外化的结果，进入该项目 `exports/`；
- 只有跨项目汇总、会议分发副本或不拥有项目状态的过手包，才进入操作方显式指定的项目外目录。

当前配置没有 `output_root`；工具不得从 `workspace_root` 猜其父目录。项目外分发文件不能反向成为
canonical 事实。这只是把既有三种用途分开，没有建立新项目存储体系。

以下位置不得承载项目持久状态：浏览器 `Downloads`、源码 `archflow/`、`tests/`、`docs/`、
仓库级 `.runs/`、未分配的任意绝对路径，以及与当前项目或 run 不一致的另一个项目目录。

## 5. Studio C/S：一条 API 边界，三层权责

```text
React / Vite / three.js / rhino3dm-wasm
  apps/archflow-studio/web
                │ OpenAPI-generated SDK
                ▼
FastAPI BFF
  apps/archflow-studio/api/archflow_studio_api
  transport → routes → application → adapters
                │ 调用现有 Python owner / P036 port
                ▼
ArchFlow kernel + P036
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

### 本地 FastAPI BFF `api/`

- 负责请求验证、transport DTO、统一错误体、鉴权/CORS、SSE、任务生命周期和 HTTP 资源。
- `StudioSettings.project_dir` 或 `--project-dir` 必须显式绑定一个带 `project.json` 的 P036 项目；
  代码没有默认项目根。
- application 层组织用例，但设计状态、依赖闭包、几何、验证和 issue 仍调用 kernel owner。
- 有项目写入时必须调用 P036 port/repository，不能在 route 或 application 中直接
  `Path.write_*`。

### Kernel / P036

- kernel 决定语义、依赖、编译和验证；P036 是项目文件系统唯一通用 writer。
- `ProjectLayout` 只命名路径，不创建、不写入。
- `PersistenceDestination` 先绑定 area/run/branch；producer 未获 destination 就停止。
- CAD adapter 的例外仍受控：它只能在调用方已经分配的 speculative workspace 内写，随后由
  runner 保留执行和读回证据。

**已验证：** 当前 Studio 有读取、候选 run、program sheet（仅 local mode 可按请求写 WIP）、
以及其他 protocol 资源，但没有 issue/canonical API。  
**工作树 WIP：** `POST /api/captures` 与 P036 `WorkspaceSink.put_workspace_file` 已在当前工作树实现；
它写命名 run 的 workspace、幂等按内容命名、不进入 `/api/artifacts`、不改变 `HEAD`。在相关提交
进入 `main` 前，不把它宣称为审计基线已有能力。

## 6. canonical 与 inspection 的边界

P036 使用 ADR-007 的四种 container state：

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
[`project.issue.issue_run`](../archflow/project/issue.py)：先完整校验 run，再调用 P036
`prepare_transition`，最后对 `HEAD` 做 `compare_and_swap`。直接写 `HEAD`、`canonical/`，或因为“测试
都过了”就把一个 run 当作当前设计，均不成立。

以下操作明确**不改变 canonical**：生成候选 run、写 run record、CAD workspace、视口截图、
对比/审查、导出分享包、浏览器本地状态。它们可以提供 issue 所需的证据，但没有 issue authority。

## 7. 新功能的最小搭建流程

### 第 1 步：把需求写成一个可验收动作

先回答四个问题：谁触发、读什么、写什么、完成时用户能观察到什么。若有输出，先在第 4 节表中
找到唯一落点；找不到就停下来请项目 owner 决定，不先写临时目录。

### 第 2 步：找到 owner，默认扩展

1. 搜索 `SYSTEM_MAP.md` 中本次行为的 owner，只读对应条目及真实调用方。
2. 在 `module_registry.json` 找 capability 的 `owns`、`does_not_own`、`public_api`、`invariants`、
   `tests`。
3. 搜索 owner 的 public API 和等价实现。
4. 选择 EXTEND；REFACTOR 必须保留可观察行为；CREATE 必须写明现有 owner 为什么不能承担。
5. 所有权或 public API 要变时，先更新 registry entry，再改代码；不能并排留下旧机制。

### 第 3 步：先固定协议，不让前后端各猜一次

- 新的对外行为先决定是否属于 protocol feature；若是，在
  `archflow_studio_api/protocol.py` 暴露 capability，并同步 `PROTOCOL.md` 的 route/status/error。
- wire shape 只写在 `api/.../transport/` 的 Pydantic DTO；业务值留在 application/kernel 的普通
  domain type。
- route 只接收调用所需身份和内容，不接收客户端指定的服务器路径。
- 有持久化时先确认现有 P036 port 是否足够；不够只增加最窄的 area-bound capability，并由
  `FilesystemProjectRepository` 实现。

### 第 4 步：按层实现

```text
kernel/domain（确有语义缺口时）
→ P036 port/repository（确有新写入形状时）
→ API application
→ transport DTO + route
→ OpenAPI generated client
→ Web UI
```

不得为了 UI 方便在 BFF 重算 kernel 事实；不得为了保存按钮让浏览器拼文件路径；不得为了快速
联调手写一份 TS interface 与 Pydantic 并行。

### 第 5 步：从 FastAPI 生成客户端

在 `apps/archflow-studio/web`：

```powershell
npm run api:generate
npm run api:check
```

`api:generate` 从真实 `create_app(...).openapi()` 生成 `src/api/generated/`。UI 再通过
`src/api/client.ts` 的现有门面调用，不直接修改 generated 文件。

### 第 6 步：以行为闭环验收

最小测试组合：

- owner 的聚焦单元测试；
- 写项目时，用真实临时 P036 repository 测 wrong project/run、内容身份、重启读回和 `HEAD` 不变；
- API 测成功、错误码和 OpenAPI shape；
- Web 测交互，再跑 generated-client drift、typecheck 和 build；
- Python/registry 改动跑 `archcheck`；
- 最后只检查本次路径的 diff，不把共享工作树其他 WIP 算进结果。

## 8. 开发、联调和验证命令

### 仓库与所有权

从源码根运行：

```powershell
git status --short
git branch --show-current
git worktree list
py -3.12 tools/devctl.py status
py -3.12 tools/archcheck.py
```

只有修改 `module_registry.json`、work registry 或 `archflow/semantics/` 后才重渲染地图：

```powershell
py -3.12 tools/devctl.py render-map
```

### Studio 一键和手动联调

一键启动读取 `apps/archflow-studio/runtime.json`：

```powershell
powershell.exe -ExecutionPolicy Bypass -File apps/archflow-studio/launch-studio.ps1
```

手动启动 API（在 `apps/archflow-studio/api`）：

```powershell
$env:ARCHFLOW_STUDIO_PROJECT_DIR = "<明确选择的 P036 project root>"
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = "<可选的 existing run id>"
py -3.12 -m archflow_studio_api.main
```

另一个终端在 `apps/archflow-studio/web`：

```powershell
npm install
npm --prefix tools/openapi-ts install
npm run dev
```

启动后先核验服务身份和 capability，而不是从 404 猜功能：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/protocol
```

### 聚焦验证

```powershell
py -3.12 -m unittest discover -s apps/archflow-studio/api/tests -t apps/archflow-studio/api -v
py -3.12 -m unittest discover -s tests -v
```

在 `apps/archflow-studio/web`：

```powershell
npm test
npm run api:check
npm run typecheck
npm run build
```

候选执行、截图和 program input 保存都会写绑定项目。联调写路径时应绑定明确准备好的测试项目或
临时 P036 副本；只读查看才可安全指向不准备新增 run/workspace 文件的真实项目。

## 9. 当前已发现但本文不替代修复的错位

1. **外部 runtime CLI 文档错位。** `config/README.md` 与 `pyproject.toml` 仍声明
   `archflow-runtime = archflow.project.runtime:main`，但 live tree 没有
   `archflow/project/runtime.py`，实现已在 `archive/`。因此本文不把该 CLI 列为当前可用的新项目
   bootstrap 命令；修复前应使用已存在的 P036 项目和显式 `project_dir`，不要重新造 bootstrap。
2. **计划不是运行时事实。** P108 和 `CANONICAL_SPINE.md` 记录了决定及迁移过程；当前服务接口、
   owner 和测试应以 protocol、registry、代码与实际生成客户端为准。
3. **Studio 早期 round-1 描述低估了现有非 canonical 写入。** 当前 protocol 已包括候选 run、
   local-only program sheet 和工作树 capture；关键不变量仍是“没有 Studio issue/canonical route”。

这些错位应各自通过其现有 owner 做小修，不在本指南旁边再建一份 runtime、artifact 或协议实现。
