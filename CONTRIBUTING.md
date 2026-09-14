# 协作流程

[`AGENTS.md`](AGENTS.md) 保留长期规则；本文说明一次任务如何交接和集成。
`module_registry` 记录软件归口与公开契约，`work_registry` 只记录当前源码并发与写入范围，
`architecture_policy` 配置静态检查。模块 owner 是软件职责，可以包含多个实现文件，不是个人姓名。

## 当前任务编号规则

MonkeyHub 的新任务以 **GitHub Issue** 为唯一 canonical task identity；Pull Request 是实现与 review 单元。
历史上的 R/M/P 工作卡继续有效，但编号已经冻结：已有 `R###`、`M###`、`P###` 可继续收尾，**不再创建 `P116+` 或新的 M/R 卡**。`P115` 只保留既有 capability-consolidation 工作，不再作为所有未来需求的默认容器。

需要在提交或机器登记中表达 GitHub Issue 时，使用 `GH-<issue-number>`；同一 Issue 有并行 lane 时可使用 `GH-<issue-number>/<lane>`。`archcheck --changed` 对 GH work id 与既有 P 卡执行同一套历史 write-scope 校验；既有 P 卡提交继续沿用原身份，不重编号历史 commit/card。

职责分工：

- GitHub Issue：需求、讨论、验收、优先级、canonical task identity；
- Pull Request：一个可 review 的实现切片；
- `work_registry.json`：只在源码工作真正开始时登记 branch/worktree/write_scope/依赖/交接，不是第二份 backlog；
- `module_registry.json`：长期软件 owner 与公开契约；
- Git history：已完成工作的实现历史。

## 贡献许可

MonkeyHub 的第一方代码以 **AGPL-3.0-only** 发布，同时保留未来提供独立商业许可的可能。为避免多人贡献后无法统一授权，新贡献者在提交代码、文档、测试或其他可版权材料前应阅读并同意 [`CLA.md`](CLA.md)。

贡献者**保留自己的版权**；CLA 仅授予项目继续以 AGPL 发布，并在需要时提供独立商业许可所需的版权与专利许可。Pull Request 模板包含确认项。外部贡献在未确认 CLA（或另有等效书面授权）前不应合入。

提交前也请确认没有把雇主/学校的保密材料、私有项目数据、API key、个人数据、受限模型/数据集或未经授权的第三方代码带入仓库。第三方内容必须明确标注来源和许可。

1. **先取基线，再查 Issue 与 owner。** `git fetch origin main` 后记录约定的提交（如 `git rev-parse origin/main`），先读 GitHub Issue，再运行 `python tools/devctl.py work` 和 `module <目标>`。用返回的精确 module id 查契约、真实调用方及相关测试，确认当前路径重叠后再改代码。
2. **一个 Issue、短分支和独立 worktree。** 新工作先开/选 GitHub Issue；需要源码并发协调时才进入 `work_registry`。新 worktree 使用 `python tools/workspace.py create --branch codex/<issue号>-<简短名称> --base <约定提交>`；继续任务复用原检出，不共用脏 worktree，不吸收别人的 WIP。已经存在的 legacy P lane 可原地收尾，不强制迁移。
3. **按任务的窄路径修改。** 开工前写明本次 `write_scope`；policy 的 `shared_write_scope` 仍可共享，不是运行权限。共享测试、生成视图及治理文件按 policy 处理，不自动视为生产路径冲突。新 Issue 的提交使用 `GH-<n>` / `GH-<n>/<lane>`；既有 legacy P lane 继续使用 `P###/lane`。
4. **给独立功能合适的位置。** 先查现有公开函数和调用方；新的分析或出图算法可以有独立目录或外部包，通过函数、CLI、API 或 adapter 接入。不要强迫每个功能改 core 或塞进已有大文件；不要复制已有状态、持久化或发布权威。实验与接入方式见 [`labs/README.md`](labs/README.md) 和指南第 7 节。
5. **按数据用途保存。** 活跃项目使用显式外部项目根，持久数据经 `archflow.project` 的现有接口保存；只有明确晋升的输入和证据进入 `probes/`。用户设置、临时文件和 adapter workspace 沿各自已有边界。确实新增持久记录或受检查的写入点时，更新现有 kind 表或 policy，不为普通内部函数新增登记。
6. **共享接口先落主线。** 已有契约足够时各任务独立实现；不足时由现有 owner 的小型上游 PR 补齐，合入 `main` 后相关分支更新基线，不在后端分支复制接口。软件归口、公开契约或列出的测试改变时同步 module registry；内部修复不用改表。DTO 改动后运行 `api:generate` 和 `api:check`，实际对外协议变化同步 [`PROTOCOL.md`](docs/PROTOCOL.md)。前端文案沿用中英文同键表。
7. **按影响验证。** 选择受影响的行为测试、类型检查或构建；纯文档检查命令、链接、生成地图和 scoped diff。`archcheck` 检查当前静态边界，`--changed` 检查已提交范围；重复能力检查只覆盖相同声明和部分代码复制，行为正确性与语义重复仍需测试和 review。
8. **一个 reviewable slice 一个 PR。** 一般一个 Issue 对应一个 PR；只有 Issue 明确拆成多个独立切片时才开多个 PR。只 `git add -- <明确文件>`，核对 staged diff，通过相关 CI 并交独立 reviewer 后集成。PR 写清 Issue、行为、base、交接、重叠、实际检查和剩余验收。不直接 push `main`，不重写已推送历史；只在已落地的相关依赖或集成需要时同步，不做每日机械 rebase。

## 登记与查看并行任务

[`governance/work_registry.json`](governance/work_registry.json) 是**活跃源码协调表**，不是需求 backlog。一个 GitHub Issue 只有在需要声明实际源码范围、branch/worktree、并行依赖或 handoff 时才进入 registry；Issue 本身仍在 GitHub 跟踪。

现有 legacy 卡可带 `lanes` 数组。Issue-native 工作也可用 `GH-<n>` 作为 registry `id`，需要并行实现时使用同样的 `lanes` 结构。每个 lane 填写：

| 字段 | 填什么 |
| --- | --- |
| `id`、`issue` | lane 短名及对应 GitHub Issue / 约定工作项 |
| `branch`、`worktree`、`base_ref` | 本任务分支、实际独立检出、约定基线；优先记录准确提交 |
| `contributor`、`reviewer`、`handoff` | 实际责任人、审查人和交接对象与顺序；未指定 reviewer/handoff 写 `null`，不代填人名 |
| `modules`、`write_scope` | 现有 module id 与本次确实要改的窄路径；模块 owner 仍是软件职责 |
| `depends_on` | 需先完成的现有 work id / lane；没有依赖时为空数组 |
| `status`、`blocked_reason` | `planned` / `active` / `review` / `blocked` / `done`；blocked 必须说明具体缺项 |

`active` / `review` 必须有实际 `branch`、`worktree`、`base_ref` 和 `contributor`；planned/blocked 尚未分配的这些字段可写 `null`，不以占位人名假装已认领。

```powershell
python tools/devctl.py work
python tools/devctl.py work GH-56
python tools/devctl.py work P115
python tools/devctl.py work P115/team-lanes
python tools/devctl.py work --json
```

这些命令只读已登记的当前任务、基线、责任与重叠，不联网确认合并，不创建 worktree 或改 registry。现有 legacy 卡继续沿用原结构直到自然关闭；**不要为了新 GitHub Issue 创建 P116。**

`active` 与 `review` 是当前写入范围声明；`planned`、`blocked`、`done` 不占并发路径。两个当前任务声明相同非共享生产路径时，缩窄范围，或把后行工作标为 `blocked`、加入 `depends_on` 并在 `handoff` 写明先后顺序。依赖未完成的工作不能标为 active/review。上游合入后核实实际提交、更新基线和依赖，再继续；完成后释放对应 live scope，GitHub Issue/PR 仍保留历史。

`archcheck --changed <base>` 按每次提交当时的 policy 与 work scope 检查，因此历史 P 卡不会被重编号或追溯改写。新 Issue 使用 `GH-<n>` / `GH-<n>/<lane>` claim；未知或格式不完整的 GH id 不会部分匹配其他 Issue。仅规则与说明维护可明确写 `P000-governance`，准确路径由 [`tools/archcheck.py`](tools/archcheck.py) 定义。

## 接着读什么

- [`CLA.md`](CLA.md) — 贡献版权/专利授权与双重许可边界。
- [`LICENSING.md`](LICENSING.md) — AGPL 与商业许可说明。
- [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) — 什么放哪、什么不进仓库。
- [`AGENTS.md`](AGENTS.md) — 少量长期规则与项目边界。
- [`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — 环境搭建与首次跑通（队友从第 8 节开始）。
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — 对外协议，客户端能依赖什么。
- [`apps/archflow-studio/README.md`](apps/archflow-studio/README.md) — Studio 的 api / web 与 `api:check`、`typecheck`、`build`。
- [`docs/mapping/planning/`](docs/mapping/planning/) — 仍在收尾的 legacy 工作卡；它们不是新任务编号池。