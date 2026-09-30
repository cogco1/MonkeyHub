# MonkeyHub 仓库：职责、目录与拓扑

本文定义共享核心、建模、出图、Monitor、Control 和 Fab 模块的职责，说明当前目录，以及仓库拓扑重构
（milestone “Topology refactor round 1”）完成后的目标目录。当前 owner、路径和公开接口以
[module registry](../governance/module_registry.json) 为准；目录分离不表示所有规划能力已经实现。
协作规则见 [AGENTS](../AGENTS.md) 与 [CONTRIBUTING](../CONTRIBUTING.md)。

第 1–5 节描述 `main` 上的现状，第 6 节是第一轮完成后的目标，第 7 节是落地它的 Issue 与顺序。
每次搬迁在同一个 PR 里更新本文对应的部分。

## 1. 明确的能力范围

| 名称 | 负责 | 不承担 |
| --- | --- | --- |
| **ArchFlow** | 共享项目身份、文件与记录保存、版本引用、建筑事实与语义契约、真实共用的计算和外部工具接口、正式 issue | 具体建模方法、图纸布局、某一工作流的界面与交互 |
| **MonkeyArch** | 3D 建模与空间修改：任务解释、构件与空间构造、模型候选、几何编译、关系检查、模型检查与续改 | 图纸字形、笔迹、二维图形、版面及图纸集组织 |
| **MonkeyDiagram** | 图纸与图解：平立剖、家具与节点表达、PDF／图片批注、二维内容编辑、文字尺寸、视图与图形表达、排版及导出 | 隐式改变模型空间或构件；建立第二套项目保存与发布权威 |
| **MonkeyMonitor** | 跨应用用量、费用估算、调用耗时与通用算法预算建议；保留 CLI/API，用量页面由 MonkeyHub 承载 | 建筑评价、执行候选、设计接受、正式发布及项目资产存储 |
| **MonkeyControl** | 与模型无关的桌面自动化：按语义定位 Windows 界面目标、执行并核验声明的结果，回执只写入调用方指定的诊断目录（[COMPUTER_USE](COMPUTER_USE.md)） | 设计状态与项目持久化 |
| **MonkeyFab** | 闭合网格的等比缩放、打印空间内封闭拆件、装配清单，以及已切片任务上传；界面由 Hub 承载 | 建筑状态修改、项目持久化、切片和自动启动打印 |

MonkeyArch 和 MonkeyDiagram 是平行工作流。ArchFlow 提供它们共同依赖的底座。
二维图纸可以表达新的设计想法；将该想法应用到三维模型是明确的跨工作流动作。
模型派生的轴测图、透视图和截图放到图纸中时，表达工作属于 MonkeyDiagram。

建模与出图执行已迁入 `monkeyarch/` 和 `monkeydiagram/`。`archflow/` 保留共同的建筑事实、
项目契约和技术适配。仅因代码可复用，不把某个工作流的业务算法放进公共核心。

## 2. 当前源码目录（第一轮之前）

下列 Python 模块随同一 Hub 发行版本安装；Fab 保留独立 CLI，设计 Web 工作区在同一个 Hub 前端内装配。
MonkeyMonitor 的诊断服务由 Hub 管理；Hub 的 Usage 页面读取同一服务，Runtime 通过可选用量适配器记录诊断。

```text
<source-root>/
├─ archflow/                     共享项目核心、事实契约、技术接口
│  ├─ project/                   P036、refs、layout、repository、issue
│  ├─ contracts/                 共同值契约与规范化
│  ├─ state/                     建筑事实、变更契约及共享几何值
│  ├─ semantics/                 建筑实体、角色与条件词汇
│  ├─ ports/                     已有外部调用接口
│  └─ adapters/                  两条工作流实际共用的技术适配（含 CAD/OCCT 执行）
├─ monkeyarch/                   3D producer、solver、编译及运行编排
├─ monkeydiagram/                图纸投影编排、SVG 与 PNG 表达
├─ monkeymonitor/                用量、计价、算法建议接口及诊断 CLI/API
├─ monkeycontrol/                桌面自动化动作契约与执行
├─ apps/monkeyfab/               制造算法、CLI、参数和测试，默认随 Hub 打包
├─ apps/monkeyhub/               唯一应用入口：api/、desktop/、installer/、run.py、launch-hub.ps1
│  └─ web/                       唯一生产前端、依赖与构建；另有 test/、scripts/、tools/、assets/
│     └─ src/                    单一源根：Hub 导航、聊天、设置、统一语言目录与项目工作区
│        ├─ app/                 同页项目工作区组合与设计反馈
│        ├─ api/project-runtime/ 每项目独立客户端与 Runtime Provider
│        └─ workspaces/
│           ├─ monkeyarch/       三维建模交互
│           ├─ monkeydiagram/    Board 双击图页打开的精确页面编辑
│           └─ monkeyboard/      画板、方案比较与会议展示
├─ apps/archflow-studio/api/     Project Runtime：API-only，历史包名保留；每项目一个进程，由 Hub 管理
├─ apps/archflow-studio/assets/  产品图标及其生成脚本
├─ apps/shared-web/src/          Hub web 共用的外观、语言与基础样式
├─ labs/                         兴趣驱动的探索；可以导入核心，核心不反向导入
├─ scripts/dev/                  仅供开发的薄启动脚本（显式 --project-dir）；生产入口只有 MonkeyHub
├─ tools/                        对应既有能力的 CLI 与治理命令
├─ tests/                        行为和边界测试；随真实迁移同步 imports
├─ probes/                       明确晋升的项目输入与回归证据
├─ governance/                   现有模块、工作、策略三类来源
└─ docs/                         架构、目录、协议与现行工作说明
```

一个源码仓、同一发行版本可以包含多块代码。独立工作流首先要求职责、目录和依赖清楚；
是否拆成独立部署或安装包，由真实使用需要决定，不与目录划分捆绑。
`apps/archflow-studio/` 仅保留项目运行时的历史目录和 Python 包名，不再包含独立前端。

现状的问题：根目录有五个业务包；`apps/` 里同时放着产品（MonkeyHub）、服务（Project Runtime）和库
（MonkeyFab、shared-web）；Runtime 的包名还停在 Studio；测试分散在多个根下；docs 混用多种命名。
第 6、7 节给出目标和顺序。

## 3. 文件归属

下表用当前路径；搬迁只改位置，不改归属。

| 当前实现 | 职责与边界 |
| --- | --- |
| `archflow/project/`、`contracts/`、共享 `state/` 与 `semantics/` | 留在 ArchFlow。图纸可引用建筑事实；图纸排版、字形和笔迹不进入建筑 StateRecord。状态中的建模专用表示需按实际消费者单独划分，不能整目录搬走。 |
| `monkeyarch/capabilities/`、`monkeyarch/compilers/geometry.py`、`monkeyarch/runtime/project_runner.py` | 3D 生成、求解、重建语义、关系检查、编译和运行。原 `archflow` 中的对应生产文件已退役，调用方直接导入新位置。 |
| `monkeydiagram/drawing_elevation.py`、`monkeydiagram/drawing_svg.py` | 图纸来源核验、模型轴立面投影编排、SVG／PNG 表达。两位既有 owner 保持原 API 和记录语义，不复制 renderer。 |
| `archflow/state/geometry_program.py` 的 `CompiledGeometryProgram` 等值 | 三维编译器与共享 CAD 执行器共用的结果契约。数据值留在 ArchFlow，生成这些值的编译算法归 MonkeyArch。 |
| `adapters/cad_execution.py`、`three_dm_inspector.py`、`ports/model.py` | 已被两条链使用的技术部分留在 ArchFlow。模型生成与二维投影的领域规则分别归各工作流；按函数职责处理混合文件，不整份复制。 |
| Web 的 `ThreeDmViewport`、Program／Options、模型 `Annotate`／`useModelAnnotations` | 归 `workspaces/monkeyarch/`；通用三维显示器若有实际共享消费者，可以继续共用。 |
| Web 的 `DocumentCanvas`、`DocumentTextLayer`、`documentInk`、`documentVisualInput`、`useDocumentAnnotations` | 已在 `workspaces/monkeydiagram/`。模型修改提交仍是显式交给 MonkeyArch 的动作，不能误称为重新出图。 |
| Hub 导航／聊天／设置、ProjectWorkspace 与生成 SDK | 归 `hub.shell`，同一前端直接渲染 Arch 和 Board；Diagram 是 Board 图页编辑。每项目 Provider 固定 API 地址与连接身份，工作区切换保留本地草稿。 |
| API 的 `application/artifacts.py`、`gestures.py`、`jobs.py` 等混合文件 | 公共文件访问、实际共用的排队／事件机制留在宿主或已有底座；模型用例归 MonkeyArch，图纸用例归 MonkeyDiagram。当前 job 合同仍偏向 candidate，不能先当成已完成的通用绘图任务接口。拆现有函数和调用，不复制保存、锁或来源校验。 |

API 中的装配用例仍保留一位 owner；拆出混合文件中的具体方法，应随下一项真实用例进行，
不能为目录对称复制 DTO、来源校验或保存流程。HTTP 接口及客户端契约保持不变。
模块 ID 不因产品名而改名；每次搬迁把 `owner_path`、调用方和公开类型的路径同步到注册表。

## 4. 依赖方向与交接

```text
MonkeyHub 前端       → Project Runtime API（经 Hub 转发路径）
Project Runtime      → monkeyarch / monkeydiagram / archflow
MonkeyArch 工作区    → monkeyarch    → archflow
MonkeyDiagram 工作区 → monkeydiagram → archflow
                     MonkeyHub 负责界面装配
MonkeyHub Usage 页   → monkeymonitor ← Project Runtime 元数据适配器
```

- ArchFlow 不导入两个工作流的内部代码；底座所需领域行为通过已有或实际需要的明确接口传入。
- MonkeyMonitor 不导入建筑核心或设计工作流；它读取用量值，返回估价与动作建议，由宿主决定执行。
- MonkeyControl 只接收动作值，由 Hub 调用：它不导入两个工作流、Monitor、Fab、Runtime 或 Hub，核心与两个工作流也不导入它。
- 两个工作流不直接导入对方内部模块。模型到图纸传递明确的模型来源与视图输入；图纸要求改模型时，
  通过 MonkeyArch 的公开动作提交。必要的新接口与首个真实消费者一起形成。
- MonkeyDiagram 保存自己的图纸修订；查看某个模型不会悄悄替换图纸的来源。更新产生新图，旧图与批注仍可追溯。
- 共享是由真实消费者证明的职责。只有一个工作流使用的业务逻辑留在该工作流，不为了“以后能共用”提前抽进核心。
- archcheck 按 policy 的 `forbidden_layer_imports` 拒绝底座反向导入工作流、两个工作流互导，以及核心、两个工作流、
  `apps/`、`tools/` 和 `tests/` 导入 `labs/`。

## 5. 项目数据、测试与发布文件

| 内容 | 放置方式 |
| --- | --- |
| 活跃模型、图纸、批注、候选、保留证据 | 显式外部项目根，沿同一 P036 项目格式。按所属 run 保存，图纸成果用已有 `workspaces/documentation/` 与 records；不建三个品牌数据仓。 |
| 项目专用建模／出图消费者 | 项目工作区中已明确的源码位置；通用算法进入相应 owner。SML 的特定拼装规则不作为公共模块默认值。 |
| 论文、私人研究资料 | 作者指定的学术工作区；不固定到某个历史 `paper/` 目录，也不成为应用启动依赖。 |
| 测试 | Python、API、Web 现有测试目录；合成 fixture 可以随测试提交。真实项目输入只有明确晋升后进入 `probes/`。 |
| 应用图标、字体、模板资源 | 实际运行需要的资源随相应应用或工作区提交；项目生成的图纸不是应用资源。 |
| 第三方许可与上游说明 | `apps/monkeyhub/installer/third-party/`，随安装包分发；archcheck 不把其中的文件当作本仓源码。 |
| 构建、日志、缓存、临时检查输出 | 配置的外部 runtime/cache/temp 或现有忽略目录；没有完成交接的源码和唯一回归不能作为可丢缓存删除。 |
| MonkeyMonitor 用量日志 | 显式指定的外部诊断目录内 `usage.jsonl`；不含提示词或项目内容，不成为 P036 资产或新的项目权威。详见 [运行与算法方案](../monkeymonitor/README.md)。 |
| 软件 release | 准确 Git 提交对应的源码／构建包及发行说明；软件版本独立于协议版本、记录 schema 和项目 HEAD。 |

GitHub Issue 跟踪任务，work registry 只登记正在改源码的 claim，架构方案解释边界与取舍；完成的 claim 从 registry 删除，结果保留在 Issue、PR 与 Git。
退役前核对真实调用、公开契约和保留数据。普通旧代码可由 Git 找回；私人原件和唯一临时材料先确认交接，
不根据“零 import”自动删除，不要求每次修复另建归档台账。

## 6. 目标拓扑（第一轮完成后）

`apps/` 只放产品，`services/` 放由 Hub 启动的服务，`packages/` 放库。

```text
<source-root>/
├─ apps/monkeyhub/                  产品：api/ web/ desktop/ installer/ assets/ run.py launch-hub.ps1
│  └─ web/                          单一源根：src/ test/ scripts/ tools/ assets/
├─ services/project-runtime/        src/project_runtime/  tests/  README.md  requirements.txt
├─ packages/
│  ├─ archflow/                     src/archflow/{contracts,project,state,semantics,validation,submission,ports,relations,adapters}
│  ├─ monkeyarch/                   src/monkeyarch/（第一轮保持原内部结构）
│  ├─ monkeydiagram/                src/monkeydiagram/
│  ├─ monkeymonitor/  monkeycontrol/  monkeyfab/    src/<包名>/
│  └─ web-shared/                   src/（Hub web 用相对路径引用）
├─ labs/                            包名保持 snake_case
├─ probes/                          不改名
├─ tests/                           只留跨 owner 的测试，如 integration/、packaging/
├─ tools/{dev,project,governance,release,benchmarks}/   模块名保持 snake_case
├─ scripts/dev/
├─ docs/{architecture,product,protocols,development,design,research,audits,decisions,prototypes}/ 与 README.md
└─ governance/                      三个 JSON 保持原名
```

- 每个 Python 包是 `packages/<包名>/`，含 `src/<包名>/`、自己的 `pyproject.toml` 和只测本包的 `tests/`。
  包的导入名不变：仍是 `archflow`、`monkeyarch`、`monkeydiagram`、`monkeymonitor`、`monkeycontrol`、`monkeyfab`。
  Runtime 改名为 `project_runtime`（原 `archflow_studio_api`）；tools 分组后导入路径变为 `tools.<组>.<模块>`。
- `src/` 布局的导入方式按场景区分（#488）：安装包把 `packages/<包名>/src/<包名>` 复制到包根，`python313._pth`
  不变（Runtime 按仓库相对路径放入安装包，`._pth` 改一行）；CI 逐包 `pip install -e`；本地开发按检出配置源码路径，
  不在共享解释器上做 editable install，否则几十个 worktree 会互相串用代码。生产代码不再写 `sys.path`。
- 根 `tests/` 只保留跨 owner 的测试；只属于一个包或服务的测试随它搬走，基准驱动去 `tools/benchmarks/`。
- `docs/` 根目录只剩 `README.md` 索引；其余文档按类别放进子目录，ADR 改为 `docs/decisions/NNN-*.md`。

第二轮做内部拆分，在第一轮之后每项另开 Issue、单独 PR，范围在开工前确认。候选项有：CAD 从
`archflow/adapters/` 抽成 `packages/monkeycad/`（第一轮的 Step 0，#485，先把 CAD 的版本声明移进内核）、
MonkeyArch 按层整理、Runtime 内部分层并把业务逻辑按函数归还 owner、`hub.shell` 分组、模块 ID 规范化。
第一轮只有 R1-9 随 tools 分组更新 `tools.*` 模块 ID（#495），其他模块 ID 不变。

### 6.1 命名规则

| 对象 | 规则 |
| --- | --- |
| Python 包和模块 | snake_case（语言规则），包括 `labs/<名字>/` 与 `tools/` 下的模块 |
| docs 文件与非代码目录 | 小写 kebab-case，例如 `services/project-runtime/`、`packages/web-shared/`、`docs/**`；docs 文件名不带日期前缀，`README.md` 保留原名 |
| 其他代码文件 | 沿用各自语言的惯例；TS/React 文件不批量改名 |
| 例外 | 工具规定的文件名、生成文件、第三方原名，以及写进安装约定的名字：`OPEN_MONKEYHUB.cmd`、`MonkeyHub.exe`、`INSTALL_MONKEYHUB.cmd`、`_runtime/…`、`Cargo.toml`、`tauri.conf.json` 等 |

以下只搬不改：P036、StateRecord、Stage／candidate／HEAD、协议与 API 路径、settings 文件的位置与格式、
已有用户数据。看起来像旧名、但已写入数据或线上接口的名字也保留：`studio-*` 记录类型和 run id、
`archflow-studio.*` localStorage 键、`service: "archflow-studio-api"`、`studioPort`、`/studio/`、
`ARCHFLOW_STUDIO_*`。全局替换只针对两个字面值：`archflow_studio_api` 和 `apps/archflow-studio/api`。

## 7. 第一轮：只搬迁

每条 lane 都遵守：

- import、路径和链接随文件在同一个 PR 里改到新位置；不留兼容 shim、别名包、转发存根或占位文档。
- registry、policy、CI、打包器和全部引用方与对应搬迁在同一个 PR 里落地；配置的路径缺失时 archcheck 报错，不跳过。
- 安装包内部布局第一轮不变：打包器把新源码路径映射回原位置，已安装更新器的 `REQUIRED_FILES` 和 `updates.rs` 不改。
- 在途 PR 冻结到拓扑落地，之后按新路径重放。

### 7.1 目录级迁移映射

| 当前 | 目标 | Issue |
| --- | --- | --- |
| `archflow_studio_api` 中 Hub 设置的持久化，以及 `routes/settings.py`、`transport/settings.py` | `apps/monkeyhub/api/monkeyhub_api/settings/`；Runtime 自己的 `GET/PUT /api/settings/user` 与 `user-settings` 能力退役 | #486 |
| `apps/shared-web/` | `packages/web-shared/` | #487 |
| `apps/archflow-studio/assets/` | `apps/monkeyhub/assets/` | #487 |
| `monkeydiagram/` | `packages/monkeydiagram/src/monkeydiagram/` | #488 |
| `archflow/`（连同 `adapters/`） | `packages/archflow/src/archflow/` | #489 |
| `monkeyarch/`、`monkeymonitor/`、`monkeycontrol/` | `packages/<包名>/src/<包名>/` | #490 |
| `apps/monkeyfab/`（`src/monkeyfab/`、`tests/`、`pyproject.toml`） | `packages/monkeyfab/`；安装包内仍是 `apps/monkeyfab/` | #490 |
| `apps/archflow-studio/api/archflow_studio_api/` | `services/project-runtime/src/project_runtime/` | #491 |
| `apps/archflow-studio/api/tests/`、`apps/archflow-studio/api/requirements.txt`、`apps/archflow-studio/README.md` | `services/project-runtime/{tests/,requirements.txt,README.md}`；`apps/archflow-studio/` 删除 | #491 |
| `apps/monkeyhub/web/workspaces/src/`、`workspaces/test/` | `apps/monkeyhub/web/src/`、`web/test/`，按子树平移，不改文件名；会与 Hub 自己的文件同名的放进各自目录：`api/` → `src/api/project-runtime/`，`styles.css` → `src/app/styles.css` | #492 |
| `apps/monkeyhub/web/workspaces/{scripts,tools,assets}/` | `apps/monkeyhub/web/{scripts,tools,assets}/`；Runtime 的 OpenAPI schema 生成到 `web/.generated/project-runtime/` | #492 |
| 根 `tests/` 中只测一个包、且不借用其他测试 helper 的文件（含 `tests/monkeycontrol/`） | `packages/<包名>/tests/`，随该包搬迁 | #488–#490 |
| 根 `tests/` 中其余测试 | 只属于一个 owner 的去该 owner 的 `tests/`；互相借用 helper 的一组整体进 `tests/integration/`；测 tools 的进 `tools/tests/`；打包测试进 `tests/packaging/`；基准驱动与数据进 `tools/benchmarks/` | #493 |
| `docs/` 根目录的大写与日期前缀文件、`docs/testing/` | `docs/{architecture,product,protocols,development,design,research,audits}/`，小写 kebab 文件名；新增 `docs/README.md` | #494 |
| `docs/adr/ADR-NNN-*.md`、`docs/CANONICAL_SPINE.md` | `docs/decisions/NNN-*.md`；`CANONICAL_SPINE` 并入 `001` | #494 |
| `docs/REPO_LAYOUT.md`（本文） | `docs/architecture/repository-layout.md`；policy 的 `unclaimed_write_scope` 与 archcheck 的 `ROOT_ENTRY` 提示同步 | #494 |
| `tools/*.py` | `archcheck`、`devctl` → `tools/governance/`；`workspace` → `tools/dev/`；`package_monkeyapps` → `tools/release/`；项目 CLI → `tools/project/`；`benchmark_*`、`projection_check` → `tools/benchmarks/` | #495 |

不变：`apps/monkeyhub/{api,desktop,installer,run.py,launch-hub.ps1}`、`labs/`、`probes/`、`scripts/dev/`、
`governance/`、`.github/`、`.claude/`、`.codex/` 与根文件。

### 7.2 顺序

各 PR 挂自己的 Issue 并登记 claim。main 要求分支与之同步，所以串行合并；同时就绪的几条走一个集成 PR。
合并顺序：R1-0 → Step 0 / R1-1a / R1-1b → R1-2 → R1-3 → R1-4 → R1-5 → R1-6 → R1-7 → R1-8 → R1-9 → R1-10。

| 步骤 | Issue | 内容 | 必须先落地 |
| --- | --- | --- | --- |
| R1-0 | #484 | archcheck 护栏与本文：配置路径缺失即报错、`python_source_roots`、`ROOT_ENTRY`、`REGISTRY_PATH_MISSING`；删除死规则与悬空依赖 | — |
| Step 0 | #485 | CAD 的版本引用声明移入 `archflow/project`，内核不再为此导入 CAD 代码 | — |
| R1-1a | #486 | Hub 设置归 Hub | — |
| R1-1b | #487 | `packages/web-shared` 与 Hub 图标资源；可与 R1-1a 并行开发 | — |
| R1-2 | #488 | 按检出配置源码根；MonkeyDiagram 作 src 布局试点 | R1-0 |
| R1-3 | #489 | `archflow/` → `packages/archflow` | R1-2 |
| R1-4 | #490 | monkeyarch、monkeymonitor、monkeycontrol、monkeyfab → `packages/` | R1-3 |
| R1-5 | #491 | Project Runtime → `services/project-runtime`（`project_runtime`） | R1-1a、R1-4 |
| R1-6 | #492 | Hub web 单一源根 | R1-1b |
| R1-7 | #493 | 测试随 owner | R1-4、R1-5：测试要进的包和服务目录已存在 |
| R1-8 | #494 | docs 分类与命名，打开 docs 检查 | R1-2 至 R1-5，免得指向代码的链接改两遍 |
| R1-9 | #495 | tools 按用途分组 | R1-5 |
| R1-10 | #496 | 去掉 `legacy_root_packages` 棘轮，确认没有遗留目录和引用，发布第一轮报告 | 以上全部 |

archflow 搬迁（R1-3）和 Runtime 改名（R1-5）这两个 PR 里，CI 的 projection-parity 检查用 base 的旧布局和候选的新布局
同时运行，需要临时识别两种布局；下一个 PR 删除这段逻辑。每次搬迁的完成标准是新位置能独立测试、
宿主经明确入口调用、原使用流程仍可运行，不是新目录已经出现。

### 7.3 archcheck 如何守住布局

`governance/architecture_policy.json` 里的这些键随搬迁修改，archcheck 把遗漏报成 finding：

| 键 | 含义与每次搬迁要做的事 |
| --- | --- |
| `repository_root_entries` | 根目录允许的条目：第 6 节的目标目录加上现有根文件。`ROOT_ENTRY` 按 `git ls-files` 检查，被 git 忽略的本地文件不算 |
| `legacy_root_packages` | 五个旧根包的棘轮。每次搬迁删掉自己那一项；包已离开根目录而这里还列着，同样报 `ROOT_ENTRY`。R1-10 删除这个键 |
| `python_source_roots` | 模块导入名从哪一级目录开始算，当前是 `.`、`apps/archflow-studio/api`、`apps/monkeyhub/api`、`apps/monkeyfab/src`。src 布局的搬迁加上自己的 `packages/<包名>/src`，R1-4 用 `packages/monkeyfab/src` 替换 `apps/monkeyfab/src`，R1-5 用 `services/project-runtime/src` 替换 `apps/archflow-studio/api`；含受检 Python 的 `src` 目录不在表里时报 `POLICY_PATH_MISSING` |
| `checked_source_roots`、`forbidden_layer_imports` 的 `source` | 路径不存在、检查根下没有 Python 源码、或层规则匹配不到任何受检文件时报 `POLICY_PATH_MISSING`；`allowed_write_sites` 与 `allowed_authority_symbols` 的文件缺失时 archcheck 直接以错误退出 |

module registry 里的路径同样必须存在：`owner_path`（`REGISTRY_OWNER_MISSING`）、`tests`（`REGISTRY_TEST_MISSING`），
以及 `files`、`used_by`（路径或模块 id）、`spine`、interface 实现文件和 capability 测试（`REGISTRY_PATH_MISSING`）。
registry 路径不写通配符。docs 根目录、文件名与日期前缀的检查在 R1-8 打开。
