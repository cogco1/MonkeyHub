# MonkeyHub 仓库：职责、目录与拓扑

本文定义共享核心、建模、出图、Monitor、Control 和 Fab 模块的职责，说明当前目录与拓扑，并记录仓库拓扑重构
第一轮（milestone “Topology refactor round 1”）如何落地。当前 owner、路径和公开接口以
[module registry](../../governance/module_registry.json) 为准；目录分离不表示所有规划能力已经实现。
协作规则见 [AGENTS](../../AGENTS.md) 与 [CONTRIBUTING](../../CONTRIBUTING.md)。

第 1–6 节描述 `main` 上的现状，第 7 节记录已经完成的第一轮：规则、旧路径到新路径的映射与顺序。
改动目录的 PR 在同一个 PR 里更新本文对应的部分。

## 1. 明确的能力范围

| 名称 | 负责 | 不承担 |
| --- | --- | --- |
| **ArchFlow** | 共享项目身份、文件与记录保存、版本引用、建筑事实与语义契约、真实共用的计算和外部工具接口、正式 issue | 具体建模方法、图纸布局、某一工作流的界面与交互 |
| **MonkeyArch** | 3D 建模与空间修改：任务解释、构件与空间构造、模型候选、几何编译、关系检查、模型检查与续改 | 图纸字形、笔迹、二维图形、版面及图纸集组织 |
| **MonkeyDiagram** | 图纸与图解：平立剖、家具与节点表达、PDF／图片批注、二维内容编辑、文字尺寸、视图与图形表达、排版及导出 | 隐式改变模型空间或构件；建立第二套项目保存与发布权威 |
| **MonkeyMonitor** | 跨应用用量、费用估算、调用耗时与通用算法预算建议；保留 CLI/API，用量页面由 MonkeyHub 承载 | 建筑评价、执行候选、设计接受、正式发布及项目资产存储 |
| **MonkeyControl** | 与模型无关的桌面自动化：按语义定位 Windows 界面目标、执行并核验声明的结果，回执只写入调用方指定的诊断目录（[computer use](../protocols/computer-use.md)） | 设计状态与项目持久化 |
| **MonkeyFab** | 闭合网格的等比缩放、打印空间内封闭拆件、装配清单，以及已切片任务上传；界面由 Hub 承载 | 建筑状态修改、项目持久化、切片和自动启动打印 |

MonkeyArch 和 MonkeyDiagram 是平行工作流。ArchFlow 提供它们共同依赖的底座。
二维图纸可以表达新的设计想法；将该想法应用到三维模型是明确的跨工作流动作。
模型派生的轴测图、透视图和截图放到图纸中时，表达工作属于 MonkeyDiagram。

建模与出图执行已迁入 `packages/monkeyarch/` 和 `packages/monkeydiagram/`。`packages/archflow/` 保留共同的建筑事实、
项目契约和技术适配。仅因代码可复用，不把某个工作流的业务算法放进公共核心。

## 2. 当前源码目录

下列 Python 模块随同一 Hub 发行版本安装；Fab 保留独立 CLI，设计 Web 工作区在同一个 Hub 前端内装配。
MonkeyMonitor 的诊断服务由 Hub 管理；Hub 的 Usage 页面读取同一服务，Runtime 通过可选用量适配器记录诊断。

```text
<source-root>/
├─ packages/archflow/            共享项目核心、事实契约、技术接口：src/archflow/、pyproject.toml、本包 tests/
│  └─ src/archflow/
│     ├─ project/                P036、refs、layout、repository、issue
│     ├─ contracts/              共同值契约与规范化
│     ├─ state/                  建筑事实、变更契约及共享几何值
│     ├─ semantics/              建筑实体、角色与条件词汇
│     ├─ ports/                  已有外部调用接口
│     └─ adapters/               两条工作流实际共用的技术适配（含 CAD/OCCT 执行）
├─ packages/monkeyarch/          3D producer、solver、编译及运行编排：src/monkeyarch/、pyproject.toml、本包 tests/
├─ packages/monkeydiagram/       图纸投影编排、SVG 与 PNG 表达：src/monkeydiagram/、pyproject.toml、本包 tests/
├─ packages/monkeymonitor/       用量、计价、算法建议接口及诊断 CLI/API：src/monkeymonitor/、pyproject.toml、本包 tests/
├─ packages/monkeycontrol/       桌面自动化动作契约与执行：src/monkeycontrol/、pyproject.toml、本包 tests/
├─ packages/monkeyfab/           制造算法、CLI、参数和测试，默认随 Hub 打包，安装包内仍在 apps/monkeyfab/
├─ packages/web-shared/          Hub web 共用的外观、语言与基础样式：src/，web 按相对路径引用
├─ apps/monkeyhub/               唯一应用入口：api/、desktop/、installer/、assets/、run.py、launch-hub.ps1
│  └─ web/                       唯一生产前端、依赖与构建；另有 test/、scripts/、tools/、assets/
│     └─ src/                    单一源根：Hub 导航、聊天、设置、统一语言目录与项目工作区
│        ├─ app/                 同页项目工作区组合与设计反馈
│        ├─ api/project-runtime/ 每项目独立客户端与 Runtime Provider
│        └─ workspaces/
│           ├─ monkeyarch/       三维建模交互
│           ├─ monkeydiagram/    Board 双击图页打开的精确页面编辑
│           └─ monkeyboard/      画板、方案比较与会议展示
├─ services/project-runtime/     Project Runtime：API-only，src/project_runtime/、tests/、requirements.txt、pyproject.toml；每项目一个进程，由 Hub 管理
├─ labs/                         兴趣驱动的探索；可以导入核心，核心不反向导入
├─ scripts/dev/                  仅供开发的薄启动脚本（显式 --project-dir）；生产入口只有 MonkeyHub
├─ tools/                        对应既有能力的 CLI 与治理命令，按用途分为 dev/、project/、governance/、release/、benchmarks/，测试在 tools/tests/
├─ tests/                        跨 owner 的测试：integration/、packaging/
├─ probes/                       明确晋升的项目输入与回归证据
├─ governance/                   现有模块、工作、策略三类来源
└─ docs/                         README.md 索引；architecture、product、protocols、development、design、
                                 research、audits、decisions、prototypes 九类文档
```

一个源码仓、同一发行版本可以包含多块代码。独立工作流首先要求职责、目录和依赖清楚；
是否拆成独立部署或安装包，由真实使用需要决定，不与目录划分捆绑。
`services/project-runtime/` 是 Hub 按项目启动的服务，只提供 API，不含前端。

## 3. 文件归属

下表用当前路径；搬迁只改位置，不改归属。

| 当前实现 | 职责与边界 |
| --- | --- |
| `packages/archflow/src/archflow/project/`、`contracts/`、共享 `state/` 与 `semantics/` | 留在 ArchFlow。图纸可引用建筑事实；图纸排版、字形和笔迹不进入建筑 StateRecord。状态中的建模专用表示需按实际消费者单独划分，不能整目录搬走。 |
| `packages/monkeyarch/src/monkeyarch/` 的 `capabilities/`、`compilers/geometry.py`、`runtime/project_runner.py` | 3D 生成、求解、重建语义、关系检查、编译和运行。原 `archflow` 中的对应生产文件已退役，调用方直接导入新位置。 |
| `packages/monkeydiagram/src/monkeydiagram/` 的 `drawing_elevation.py`、`drawing_svg.py` | 图纸来源核验、模型轴立面投影编排、SVG／PNG 表达。两位既有 owner 保持原 API 和记录语义，不复制 renderer。 |
| `packages/archflow/src/archflow/state/geometry_program.py` 的 `CompiledGeometryProgram` 等值 | 三维编译器与共享 CAD 执行器共用的结果契约。数据值留在 ArchFlow，生成这些值的编译算法归 MonkeyArch。 |
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
| MonkeyMonitor 用量日志 | 显式指定的外部诊断目录内 `usage.jsonl`；不含提示词或项目内容，不成为 P036 资产或新的项目权威。详见 [运行与算法方案](../../packages/monkeymonitor/src/monkeymonitor/README.md)。 |
| 软件 release | 准确 Git 提交对应的源码／构建包及发行说明；软件版本独立于协议版本、记录 schema 和项目 HEAD。 |

GitHub Issue 跟踪任务，work registry 只登记正在改源码的 claim，架构方案解释边界与取舍；完成的 claim 从 registry 删除，结果保留在 Issue、PR 与 Git。
退役前核对真实调用、公开契约和保留数据。普通旧代码可由 Git 找回；私人原件和唯一临时材料先确认交接，
不根据“零 import”自动删除，不要求每次修复另建归档台账。

## 6. 拓扑

第一轮之后仓库根目录只有下面这些条目，policy 的 `repository_root_entries` 列的正是它们（6.3）。
`apps/` 只放产品，`services/` 放由 Hub 启动的服务，`packages/` 放库。

```text
<source-root>/
├─ apps/monkeyhub/                  产品：api/ web/ desktop/ installer/ assets/ run.py launch-hub.ps1
│  └─ web/                          单一源根：src/ test/ scripts/ tools/ assets/
├─ services/project-runtime/        src/project_runtime/  tests/  README.md  requirements.txt  pyproject.toml
├─ packages/
│  ├─ archflow/                     src/archflow/{contracts,project,state,semantics,validation,submission,ports,adapters}
│  ├─ monkeyarch/                   src/monkeyarch/（第一轮保持原内部结构）
│  ├─ monkeydiagram/                src/monkeydiagram/
│  ├─ monkeymonitor/  monkeycontrol/  monkeyfab/    src/<包名>/
│  └─ web-shared/                   src/（Hub web 用相对路径引用）
├─ labs/                            包名保持 snake_case
├─ probes/                          不改名
├─ tests/                           只留跨 owner 的测试：integration/、packaging/
├─ tools/{dev,project,governance,release,benchmarks}/   模块名保持 snake_case；测 tools 的在 tools/tests/
├─ scripts/dev/
├─ docs/{architecture,product,protocols,development,design,research,audits,decisions,prototypes}/ 与 README.md
├─ governance/                      三个 JSON 保持原名
├─ .github/  .claude/  .codex/      CI 工作流与 PR 模板；Claude、Codex 的仓库配置
└─ 根文件                           AGENTS.md  CLA.md  CONTRIBUTING.md  LICENSE  LICENSING.md  OPEN_MONKEYHUB.cmd
                                    README.md  SECURITY.md  conftest.py  pyproject.toml  .gitignore
```

- 每个 Python 包是 `packages/<包名>/`，含 `src/<包名>/`、自己的 `pyproject.toml` 和只测本包的 `tests/`。
  包的导入名没变：仍是 `archflow`、`monkeyarch`、`monkeydiagram`、`monkeymonitor`、`monkeycontrol`、`monkeyfab`。
  Runtime 的包名是 `project_runtime`（#491 之前是 `archflow_studio_api`）；tools 的导入路径是 `tools.<组>.<模块>`。
- `src/` 布局的导入方式按场景区分（#488）：安装包里的位置见 6.2；
  CI 逐包 `pip install -e`（Runtime 的依赖仍由 `services/project-runtime/requirements.txt` 安装，它的 `pyproject.toml`
  从同一文件读依赖），根 `pyproject.toml` 只剩开发与测试配置；本地开发按检出读取 policy 的 `python_source_roots`，把本检出的源码根放到
  `sys.path` 最前：根 `conftest.py`、`tests/__init__.py`、导入领域包的 tools、Hub 的测试以及测试与浏览器测试另起的
  Python 进程调用 `tools/dev/source_roots.py`（只测一个包的子进程直接用本包的 `src`），`apps/monkeyhub/run.py`、MCP 启动与
  Runtime 包在能导入任何东西之前自己读同一张表；Runtime 的测试包先把服务的 `src` 放上 `sys.path`，再导入 Runtime 包。不在共享解释器上做
  editable install，否则几十个 worktree 会互相串用代码。安装包不带 policy，生产入口在那里不改 `sys.path`，只由 `._pth` 决定。
- 根 `tests/` 只保留跨 owner 的测试；只属于一个包或服务的测试在它自己的 `tests/`，测 tools 的在 `tools/tests/`，
  基准驱动在 `tools/benchmarks/`。
- `docs/` 根目录只有 `README.md` 索引；其余文档在类别子目录里，决定记录是 `docs/decisions/NNN-*.md`。

第二轮做内部拆分，每项另开 Issue、单独 PR，范围在开工前确认。候选项有：CAD 从
`packages/archflow/src/archflow/adapters/` 抽成 `packages/monkeycad/`（第一轮的 Step 0，#485，先把 CAD 的版本声明移进内核）、
MonkeyArch 按层整理、Runtime 内部分层并把业务逻辑按函数归还 owner、`hub.shell` 分组、模块 ID 规范化。
第一轮不改模块 ID：R1-9（#495）分组后 `tools.*` 仍是原 ID（如 `tools.archcheck`），只更新 `owner_path` 等路径，随 ID 规范化一起调整。

### 6.1 命名规则

| 对象 | 规则 |
| --- | --- |
| Python 包和模块 | snake_case（语言规则），包括 `labs/<名字>/` 与 `tools/` 下的模块 |
| docs 文件与非代码目录 | 小写 kebab-case，例如 `services/project-runtime/`、`packages/web-shared/`、`docs/**`；docs 文件名不带日期前缀，日期写进文首 front matter（`created:`）；决定记录为 `docs/decisions/NNN-*.md`，保留 ADR 编号；`README.md` 保留原名；非 Markdown 文件只放 `docs/prototypes/`，沿用原型自己的文件名 |
| 其他代码文件 | 沿用各自语言的惯例；TS/React 文件不批量改名 |
| 例外 | 工具规定的文件名、生成文件、第三方原名，以及写进安装约定的名字：`OPEN_MONKEYHUB.cmd`、`MonkeyHub.exe`、`INSTALL_MONKEYHUB.cmd`、`_runtime/…`、`Cargo.toml`、`tauri.conf.json` 等 |

以下只搬不改：P036、StateRecord、Stage／candidate／HEAD、协议与 API 路径、settings 文件的位置与格式、
已有用户数据。看起来像旧名、但已写入数据或线上接口的名字也保留：`studio-*` 记录类型和 run id、
`archflow-studio.*` localStorage 键、`service: "archflow-studio-api"`、`studioPort`、`/studio/`、
`ARCHFLOW_STUDIO_*`。Runtime 改名（R1-5）只替换了 `archflow_studio_api` 和 `apps/archflow-studio/api` 两个字面值。

### 6.2 安装包里的位置

安装包（`tools/release/package_monkeyapps.py`）里有两处 Python 源码有意不放在仓库路径：

| 仓库 | 安装包内 | 为什么 |
| --- | --- | --- |
| `packages/<包名>/src/<包名>/`：archflow、monkeyarch、monkeydiagram、monkeymonitor、monkeycontrol | 包根 `<包名>/`（`BUNDLED_PACKAGES`） | 内置解释器的 `python313._pth` 用 `..\..` 找到它们。第一轮只搬仓库、不动安装包布局，所以这些搬迁既没改 `._pth`，也没改已安装版本里这些文件的位置 |
| `packages/monkeyfab/` | `apps/monkeyfab/`（`FAB_BUNDLE`） | 已安装更新器的 `REQUIRED_FILES`（`apps/monkeyhub/installer/patch.py`）和 `install.ps1` 的必需文件列出 `apps/monkeyfab/src/monkeyfab/__main__.py` 与 `apps/monkeyfab/pyproject.toml`，已装版本拒收缺了它们的安装包；`._pth` 也列出 `..\..\apps\monkeyfab\src`。Hub 在检出里用 `packages/monkeyfab/src`，在安装包里用 `apps/monkeyfab/src`（`monkeyhub_api/fabrication.py` 的 `FAB_SOURCES`） |

Project Runtime 自 #491 起按仓库路径 `services/project-runtime/src/project_runtime` 进入安装包：`._pth` 为它加了
`..\..\services\project-runtime\src`，随补丁送达，已安装更新器不检查 Runtime 的路径。Hub 的 `api/`、`installer/`、
`assets/` 与随带的 `tools/project/{create_project,run_project}.py`、`tools/dev/source_roots.py` 也在仓库路径。
让五个包回到仓库路径，要像 #491 那样改 `._pth` 并随补丁发出；MonkeyFab 则要先让已安装的更新器不再要求
`apps/monkeyfab/`。两者都不属于第一轮。

### 6.3 archcheck 如何守住布局

`governance/architecture_policy.json` 里的这些键描述布局，改目录的 PR 随之修改，archcheck 把遗漏报成 finding：

| 键 | 含义与改目录时要做的事 |
| --- | --- |
| `repository_root_entries` | 根目录的完整清单：第 6 节的目录加上根文件。`ROOT_ENTRY` 按 `git ls-files` 检查，被 git 忽略的本地文件不算；清单外的条目都是 finding，搬回根目录的包也一样 |
| `python_source_roots` | 模块导入名从哪一级目录开始算，当前是 `.`、`services/project-runtime/src`、`apps/monkeyhub/api`、`packages/monkeyfab/src`、`packages/monkeydiagram/src`、`packages/archflow/src`、`packages/monkeyarch/src`、`packages/monkeymonitor/src`、`packages/monkeycontrol/src`。这是唯一的清单：本地开发的各入口按检出读它（第 6 节）。新的 src 布局包加上自己的 `packages/<包名>/src`；含受检 Python 的 `src` 目录不在表里时报 `POLICY_PATH_MISSING` |
| `checked_source_roots`、`forbidden_layer_imports` 的 `source` | `packages/` 下的包：检查根写包根 `packages/<包名>`，包的层规则 `source` 写 `packages/<包名>/src/<包名>`，`packages/<包名>/tests` 另有一条不导入根 `tests`、`labs`、`archive` 的规则，也和根 `tests/` 一样列入 `shared_write_scope`。Runtime 是服务：检查根和层规则的 `source` 都写服务根 `services/project-runtime`，包与它的 `tests/` 同受一条规则约束，`services/project-runtime/tests/` 列入 `shared_write_scope`。路径不存在、检查根下没有 Python 源码、或层规则匹配不到任何受检文件时报 `POLICY_PATH_MISSING`；`allowed_write_sites` 与 `allowed_authority_symbols` 的文件缺失时 archcheck 直接以错误退出 |

module registry 里的路径同样必须存在：`owner_path`（`REGISTRY_OWNER_MISSING`）、`tests`（`REGISTRY_TEST_MISSING`），
以及 `files`、`used_by`（路径或模块 id）、`spine`、interface 实现文件和 capability 测试（`REGISTRY_PATH_MISSING`）。
registry 路径不写通配符。

docs 树自 R1-8 起按 `git ls-files` 检查，被 git 忽略的本地笔记不算：根目录只有 `README.md`（`DOCS_ROOT`）；文档名是小写 kebab-case 的 Markdown，决定记录是 `NNN-kebab.md`，目录名是 kebab-case，文件名不以日期开头（kebab 正则本身接受 `2026-09-28-x.md`，所以日期另有一条规则），非 Markdown 文件只在 `docs/prototypes/`（`DOC_NAME`）。

## 7. 第一轮记录：只搬迁

第一轮（#484–#496）已全部落地。每条 lane 都遵守了：

- import、路径和链接随文件在同一个 PR 里改到新位置；不留兼容 shim、别名包、转发存根或占位文档。
- registry、policy、CI、打包器和全部引用方与对应搬迁在同一个 PR 里落地；配置的路径缺失时 archcheck 报错，不跳过。
- 已安装更新器的 `REQUIRED_FILES` 和 `updates.rs` 不改：打包器把搬走的五个包映射回包根、把 MonkeyFab 映射回
  `apps/monkeyfab/`；Runtime、Hub 图标和随带的 tools 在安装包里也换到了仓库路径（6.2）。
- 在途 PR 冻结到拓扑落地，之后按新路径重放。

### 7.1 目录级迁移映射

| 原位置 | 现位置 | Issue |
| --- | --- | --- |
| `archflow_studio_api` 中 Hub 设置的持久化，以及 `routes/settings.py`、`transport/settings.py` | `apps/monkeyhub/api/monkeyhub_api/settings/`；Runtime 自己的 `GET/PUT /api/settings/user` 与 `user-settings` 能力退役 | #486（已落地） |
| `apps/shared-web/` | `packages/web-shared/` | #487（已落地） |
| `apps/archflow-studio/assets/` | `apps/monkeyhub/assets/` | #487（已落地） |
| `monkeydiagram/` | `packages/monkeydiagram/src/monkeydiagram/`；只测本包的 7 个测试进 `packages/monkeydiagram/tests/` | #488（已落地） |
| `archflow/`（连同 `adapters/`） | `packages/archflow/src/archflow/`；只测本包的 19 个测试进 `packages/archflow/tests/` | #489（已落地） |
| `monkeyarch/`、`monkeymonitor/`、`monkeycontrol/` | `packages/<包名>/src/<包名>/`；monkeycontrol 的 11 个测试与 monkeymonitor 不借用 helper 的 4 个测试进 `packages/<包名>/tests/` | #490（已落地） |
| `apps/monkeyfab/`（`src/monkeyfab/`、`tests/`、`pyproject.toml`） | `packages/monkeyfab/`；安装包内仍是 `apps/monkeyfab/` | #490（已落地） |
| `apps/archflow-studio/api/archflow_studio_api/` | `services/project-runtime/src/project_runtime/` | #491（已落地） |
| `apps/archflow-studio/api/tests/`、`apps/archflow-studio/api/requirements.txt`、`apps/archflow-studio/README.md` | `services/project-runtime/{tests/,requirements.txt,README.md}`；`apps/archflow-studio/` 删除 | #491（已落地） |
| `apps/monkeyhub/web/workspaces/src/`、`workspaces/test/` | `apps/monkeyhub/web/src/`、`web/test/`，按子树平移，不改文件名；会与 Hub 自己的文件同名的放进各自目录：`api/` → `src/api/project-runtime/`，`styles.css` → `src/app/styles.css` | #492（已落地） |
| `apps/monkeyhub/web/workspaces/{scripts,tools,assets}/` | `apps/monkeyhub/web/{scripts,tools,assets}/`；Runtime 的 OpenAPI schema 生成到 `web/.generated/project-runtime/` | #492（已落地） |
| 根 `tests/` 中只测一个包、且不借用其他测试 helper 的文件（含 `tests/monkeycontrol/`） | `packages/<包名>/tests/`，随该包搬迁 | #488–#490（已落地） |
| 根 `tests/` 中其余测试 | 只属于一个 owner 的去该 owner 的 `tests/`；互相借用 helper 的一组整体进 `tests/integration/`；测 tools 的进 `tools/tests/`；打包测试进 `tests/packaging/`；基准驱动与数据随 #495 进 `tools/benchmarks/` | #493（已落地） |
| `docs/` 根目录的大写与日期前缀文件、`docs/testing/` | `docs/{architecture,product,protocols,development,design,research,audits}/`，小写 kebab 文件名；新增 `docs/README.md` | #494（已落地） |
| `docs/adr/ADR-NNN-*.md`、`docs/CANONICAL_SPINE.md` | `docs/decisions/NNN-*.md`；`CANONICAL_SPINE` 并入 `001` | #494（已落地） |
| `docs/REPO_LAYOUT.md`（本文） | `docs/architecture/repository-layout.md`；policy 的 `unclaimed_write_scope` 与 archcheck 的 `ROOT_ENTRY` 提示同步 | #494（已落地） |
| `tools/*.py`、`tests/monkeymonitor/` | `archcheck`、`devctl` → `tools/governance/`；`workspace`、`source_roots` → `tools/dev/`；`package_monkeyapps` → `tools/release/`；项目 CLI → `tools/project/`；`benchmark_*`、`projection_check` 与 `tests/monkeymonitor/` 的基准驱动、场景数据 → `tools/benchmarks/`，驱动的测试 → `tools/tests/`。安装包随带的 `create_project`、`run_project` 与 `source_roots` 也按新路径放在 `tools/project/`、`tools/dev/`，它们的 `tools.<组>` 导入在包内同样成立；这三个文件不在 `REQUIRED_FILES` 中 | #495（已落地） |

不变：`apps/monkeyhub/{api,desktop,installer,run.py,launch-hub.ps1}`、`labs/`、`probes/`、`scripts/dev/`、
`governance/`、`.github/`、`.claude/`、`.codex/` 与根文件。

### 7.2 顺序

各 PR 挂自己的 Issue 并登记 claim。main 要求分支与之同步，所以串行合并；同时就绪的几条走一个集成 PR。
合并顺序：R1-0 → Step 0 / R1-1a / R1-1b → R1-2 → R1-3 → R1-4 → R1-5 → R1-6 → R1-7 → R1-8 → R1-9 → R1-10。

| 步骤 | Issue | 内容 | 必须先落地 |
| --- | --- | --- | --- |
| R1-0 | #484 | archcheck 护栏与本文：配置路径缺失即报错、`python_source_roots`、`ROOT_ENTRY`、`REGISTRY_PATH_MISSING`；删除死规则与悬空依赖。已落地 | — |
| Step 0 | #485 | CAD 的版本引用声明移入 `packages/archflow/src/archflow/project`，内核不再为此导入 CAD 代码。已落地 | — |
| R1-1a | #486 | Hub 设置归 Hub。已落地 | — |
| R1-1b | #487 | `packages/web-shared` 与 Hub 图标资源；可与 R1-1a 并行开发。已落地 | — |
| R1-2 | #488 | 按检出配置源码根；MonkeyDiagram 作 src 布局试点。已落地，机制见第 6 节 | R1-0 |
| R1-3 | #489 | `archflow/` → `packages/archflow`。已落地 | R1-2 |
| R1-4 | #490 | monkeyarch、monkeymonitor、monkeycontrol、monkeyfab → `packages/`。已落地 | R1-3 |
| R1-5 | #491 | Project Runtime → `services/project-runtime`（`project_runtime`）。已落地 | R1-1a、R1-4 |
| R1-6 | #492 | Hub web 单一源根。已落地 | R1-1b |
| R1-7 | #493 | 测试随 owner：单一 owner 的测试进该 owner 的 `tests/`，测 tools 的进 `tools/tests/`，互借 helper 的一组整体进 `tests/integration/`，打包测试进 `tests/packaging/`。已落地 | R1-4、R1-5：测试要进的包和服务目录已存在 |
| R1-8 | #494 | docs 分类与命名，打开 docs 检查。已落地 | R1-2 至 R1-5，免得指向代码的链接改两遍 |
| R1-9 | #495 | tools 按用途分组：`tools/{dev,project,governance,release,benchmarks}/`，`tests/monkeymonitor/` 的基准驱动进 `tools/benchmarks/`。已落地 | R1-5 |
| R1-10 | #496 | 去掉 `legacy_root_packages` 棘轮，`repository_root_entries` 成为根目录的完整清单；去掉 CI projection 检查为 R1-3 至 R1-5 的 PR 同时认旧布局而加的逻辑；扫清仍指向旧路径的当前引用，发布第一轮报告。已落地 | 以上全部 |

每次搬迁的完成标准是新位置能独立测试、宿主经明确入口调用、原使用流程仍可运行，不是新目录已经出现。

第一轮没有留下兼容层：没有 shim、别名包、转发存根或占位文档，也没有按旧布局运行的代码路径。旧路径只作为历史出现：7.1 的映射、注明日期或基线的决定记录、设计、审计与探针文字、钉在具体提交上的永久链接、archcheck 为 #358 之前的提交保留的 `LEGACY_*` 读法、按 sha 核对的检索语料、在旧提交上运行旧布局的跨版本记忆测试，以及 6.2 所列安装包里的位置。`tools/tests` 在临时仓库里搭的合成目录树不指向本仓库，其中不少沿用搬迁前的根布局。
