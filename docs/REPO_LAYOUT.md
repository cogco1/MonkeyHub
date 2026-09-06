# 仓库文件规则 — 什么放哪,什么不进来

一句话:**仓库只放会被 import、被测试跑、被 CI 检查、或被注册表引用的东西。** 设计项目、论文、图、临时物、退役物各有自己的家。

## 1. 三个地方

| 地方 | 路径 | 放什么 | 规则文件 |
|---|---|---|---|
| 代码仓库 | `D:\ARCHFLOW_V4` | 脊柱代码、Studio、CLI、测试、治理、canonical 文档、工作卡 | 本文 + `AGENTS.md`;日常协作十条见 [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| 工作区 | `D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME` | P036 设计项目、论文、设计稿、移交文档、交付物、原始资料 | `V4_RUNTIME\README.md`、`V4_RUNTIME\GENERATION_RECORD_SPEC.md` |
| 外部归档 | `D:\PROJECTS\99_ARCHIVE_旧项目归档\ARCHFLOW_V4_ARCHIVE` | 退役的代码线、已完成的计划、被取代的文档、死测试、孤儿脚本、worktree 残骸 | 该目录 `README.md`(批次命名、索引、找回) |

## 2. 仓库顶层每个目录只负责一件事

| 目录 | 负责 | 不放 |
|---|---|---|
| `archflow/` | 生产脊柱,一个包一个职责,每个模块在 `governance/module_registry.json` 有 owner 条目 | 退役线、实验脚本、项目数据 |
| `apps/archflow-studio/` | MonkeyArch:`api/`(FastAPI)、`web/`(React)、启动器、图标、loading 帧 | 用户设置、项目文件 |
| `tools/` | 脊柱 CLI(run / verify / freeze / open / issue / reindex)与治理 CLI(archcheck、devctl) | 一次性脚本(进工作区 `temp/` 或外部归档) |
| `labs/` | 兴趣驱动的探索,`labs/<名字>/` 一人一块;可以 `import archflow.*`,不受注册表 owner 与写入点检查约束,规则与毕业方式见 [`labs/README.md`](../labs/README.md) | 被脊柱 import 的东西(`archflow`/`tools`/`apps`/`tests` 都不许 import `labs`,archcheck 会拦)、项目数据 |
| `tests/` | 脊柱测试,唯一进 CI 的套件;`tests/support.py` 是 fixture 的唯一入口;要读 P036 项目的测试自建临时项目,不读仓库里的数据 | import archive 的测试(archcheck 会拦)、数据文件、fixture 项目 |
| `governance/` | 模块注册表、架构策略、工作注册表(活着的工作,一项一张卡) | 已完成的卡(完成即删) |
| `docs/` | `adr/`(裁决)、canonical 文档(ARCHITECTURE、CANONICAL_SPINE、PROTOCOL、VISION、RESEARCH_POSITIONING)、devctl 渲染的四份图(SYSTEM_MAP、DYNAMIC_MAP、SEMANTIC_REGISTRY、mapping/planning/INDEX)、工作卡 `mapping/planning/P###-*.md`、本文 | 论文、示意图、计划备忘、会话报告、工作区规范 |
| `.github/` | CI:archcheck(PR 上另跑 `--changed` 查写入范围)、spine 套件、Studio API 套件、web 的 `api:check`/`typecheck`/`build`;PR 模板 | |
| `.codex/` | codex CLI 的项目配置与云环境脚本 | |

## 3. 计划只有一种载体

工作卡。`governance/work_registry.json` 一项对应 `docs/mapping/planning/P###-*.md` 一张,写目标、验收、写入范围;
做完连同注册表条目一起删。**不再有 `docs/claude-worktree/` 这类计划目录**:会话的计划要么折成卡,要么进外部归档。
ADR 记裁决,不记计划。

## 4. 什么永远不进仓库

- 论文与投稿材料 → 工作区 `paper/`。
- 示意图、图标方案、UI 稿 → 工作区 `design/`(仓库只保留应用实际加载的资源:`apps/archflow-studio/assets/`)。
- 设计项目的任何文件(记录、run、导出) → 工作区 `workspace/projects/`。
- 会话产物(worker 报告、review diff、截图、临时脚本) → 外部归档或工作区 `temp/`。
- 缓存、日志、构建产物(`__pycache__`、`.pytest_cache`、`dist`、`.runtime`、`node_modules`)→ `.gitignore` 已覆盖,不要 `git add -f`。
- 秘密与机器路径(token、绝对路径)→ 环境变量或本机 `runtime.json`,不入库。

## 5. 归档与退役

- 一条线不再被脊柱 import、不再被 CI 跑、论文也不再需要它重放 → 整体移到外部归档(一批一个目录,按仓库相对路径原样放,批次 `INDEX.md` 写原路径、移出提交、原因、被谁取代)。
- 单个文件零引用(canonical 文档、注册表、工具、测试都不提它)→ 同上。
- 仓库里不再保留"退役但可运行"的 `archive/` 目录;需要重放旧线时从外部归档取回到临时目录,把仓库加进 PYTHONPATH 跑。
- `labs/<名字>/` 里的东西没人 import 就直接删,不必走归档;它要留下来只有一条路——变成一张卡、一个注册表 owner,同时把 lab 里的副本删掉(见 [`labs/README.md`](../labs/README.md))。
- 归档动作一批一个提交,只 `git add` 明确路径;archcheck 与两套测试绿才算完成;不 push,push 由 Kaiwen 说。

## 6. 命名

- 记录文件:`<kind>-<sha256>.json`(P036,`archflow/project/record_kinds.py` 是唯一的 kind 表)。
- 工作卡:`P###-<slug>.md`;ADR:`ADR-###-<slug>.md`。
- 工作区交付物:`YYMMDD_用途[_形态]`;移交文档:`YYMMDD_<发起方>_<接收方>_<主题>.md`,回执同名加"回执"。
- 外部归档批次:`YYYYMMDD_<repo|worktree|runtime>_<说明>/`。
