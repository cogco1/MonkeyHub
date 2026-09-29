# 协作流程

[`AGENTS.md`](AGENTS.md) 保留长期规则；本文说明一次任务如何交接和集成。
`module_registry` 记录软件归口与公开契约，`work_registry` 只记录当前源码并发与写入范围，
`architecture_policy` 配置静态检查。模块 owner 是软件职责，可以包含多个实现文件，不是个人姓名。

## 任务身份

MonkeyHub 的任务以 **GitHub Issue** 为唯一 canonical task/spec identity；Pull Request 是实现与 review 单元。任务从 Issue 开始，完成后由 closed Issue、PR 和 Git history 记录，仓库里不另存任务卡。

提交和机器登记用 `GH-<issue-number>` 表达 Issue；同一 Issue 有并行 lane 时用 `GH-<issue-number>/<lane>`。`P###`、`M###`、`R###` 只作历史 Git／文档引用：R/M/P 工作卡已按 #358 退役，不再作为提交或登记身份；历史提交不改写、不重编号。

职责分工：

- GitHub Issue：需求、讨论、验收、task identity；
- GitHub Project（MonkeyHub Development）：优先级、状态、Initiative 与有证据的日期，规则见 [`docs/PROJECT_TRACKING.md`](docs/PROJECT_TRACKING.md)；
- Pull Request：一个可 review 的实现切片；
- `work_registry.json`：源码工作真正开始时才登记的当前 claim（branch/worktree/write_scope/依赖/交接），不是第二份 backlog，完成即删除；
- `module_registry.json`：长期软件 owner 与公开契约；
- `docs/ARCHITECTURE.md` 与 `docs/adr/`：需要长期保留的设计决定；
- Git history：已完成工作的实现历史。

## 贡献许可

MonkeyHub 的第一方代码以 **AGPL-3.0-only** 发布，同时保留未来提供独立商业许可的可能。为避免多人贡献后无法统一授权，新贡献者在提交代码、文档、测试或其他可版权材料前应阅读并同意 [`CLA.md`](CLA.md)。

贡献者**保留自己的版权**；CLA 仅授予项目继续以 AGPL 发布，并在需要时提供独立商业许可所需的版权与专利许可。Pull Request 模板包含确认项。外部贡献在未确认 CLA（或另有等效书面授权）前不应合入。

提交前也请确认没有把雇主/学校的保密材料、私有项目数据、API key、个人数据、受限模型/数据集或未经授权的第三方代码带入仓库。第三方内容必须明确标注来源和许可。

1. **先取基线，再查 Issue 与 owner。** `git fetch origin main` 后记录约定的提交（如 `git rev-parse origin/main`），先读 GitHub Issue，再运行 `python tools/devctl.py work` 和 `module <目标>`。用返回的精确 module id 查契约、真实调用方及相关测试，确认当前路径重叠后再改代码。
2. **一个 Issue、短分支和独立 worktree。** 新工作先开/选 GitHub Issue；需要源码并发协调时才进入 `work_registry`。新 worktree 使用 `python tools/workspace.py create --branch codex/<issue号>-<简短名称> --base <约定提交>`；继续任务复用原检出，不共用脏 worktree，不吸收别人的 WIP。
3. **按任务的窄路径修改。** 开工前写明本次 `write_scope`；policy 的 `shared_write_scope` 仍可共享，不是运行权限。共享测试、生成视图及治理文件按 policy 处理，不自动视为生产路径冲突。提交标题写 `GH-<n>` 或 `GH-<n>/<lane>`；不写 claim 的提交只能改 policy 的 `shared_write_scope` 与 `unclaimed_write_scope`。
4. **给独立功能合适的位置。** 先查现有公开函数和调用方；新的分析或出图算法可以有独立目录或外部包，通过函数、CLI、API 或 adapter 接入。不要强迫每个功能改 core 或塞进已有大文件；不要复制已有状态、持久化或发布权威。实验与接入方式见 [`labs/README.md`](labs/README.md) 和指南第 7 节。
5. **按数据用途保存。** 活跃项目使用显式外部项目根，持久数据经 `archflow.project` 的现有接口保存；只有明确晋升的输入和证据进入 `probes/`。用户设置、临时文件和 adapter workspace 沿各自已有边界。确实新增持久记录或受检查的写入点时，更新现有 kind 表或 policy，不为普通内部函数新增登记。
6. **共享接口先落主线。** 已有契约足够时各任务独立实现；不足时由现有 owner 的小型上游 PR 补齐，合入 `main` 后相关分支更新基线，不在后端分支复制接口。软件归口、公开契约或列出的测试改变时同步 module registry；内部修复不用改表。DTO 改动后运行 `api:generate` 和 `api:check`，实际对外协议变化同步 [`PROTOCOL.md`](docs/PROTOCOL.md)。前端文案沿用中英文同键表。
7. **按影响验证。** 选择受影响的行为测试、类型检查或构建；纯文档检查命令、链接、生成地图和 scoped diff。`archcheck` 检查当前静态边界，`--changed` 检查已提交范围；重复能力检查只覆盖相同声明和部分代码复制，行为正确性与语义重复仍需测试和 review。
8. **一个 reviewable slice 一个 PR。** 一般一个 Issue 对应一个 PR；只有 Issue 明确拆成多个独立切片时才开多个 PR。只 `git add -- <明确文件>`，核对 staged diff，通过相关 CI 并交独立 reviewer 后集成。PR 写清 Issue、行为、base、交接、重叠、实际检查和剩余验收。不直接 push `main`，不重写已推送历史；只在已落地的相关依赖或集成需要时同步，不做每日机械 rebase。

## 登记与查看并行任务

[`governance/work_registry.json`](governance/work_registry.json)（`ArchFlowDevelopmentRegistry@3`）是**活跃源码协调表**，不是需求 backlog。一个 GitHub Issue 只有在需要声明实际源码范围、branch/worktree、并行依赖或 handoff 时才进入 registry；目标与验收只写在 Issue 里。

每一项的 `id` 是 `GH-<n>`，有两种形状：

- **直接 claim**：这一项本身填写下表字段，claim 名为 `GH-<n>`；
- **按 lane claim**：这一项只有 `id` 和 `lanes`；每个 lane 以短名为 `id` 并填写下表字段，是一个独立 claim（`GH-<n>/<lane>`），只占自己的 `write_scope`。

| 字段 | 填什么 |
| --- | --- |
| `branch`、`worktree`、`base_ref` | 本任务分支、实际独立检出、约定基线；优先记录准确提交 |
| `contributor`、`reviewer`、`handoff` | 实际责任人、审查人和交接对象与顺序；未指定 reviewer/handoff 写 `null`，不代填人名 |
| `modules`、`write_scope` | 现有 module id 与本次确实要改的窄路径；模块 owner 仍是软件职责 |
| `depends_on` | 需先完成的 `GH-<n>` 或 `GH-<n>/<lane>`；没有依赖时为空数组 |
| `status`、`blocked_reason` | `active` / `review` / `blocked`；blocked 必须说明具体缺项 |

`active` / `review` 必须有实际 `branch`、`worktree`、`base_ref` 和 `contributor`；blocked 尚未分配的这些字段可写 `null`，不以占位人名假装已认领。表外字段（如 `card`、`goal`、`acceptance`、`stream`、`issue`、`notes`）不属于协调表，`archcheck` 报 `WORK_ITEM`。

```json
{
  "id": "GH-412",
  "status": "active",
  "branch": "codex/412-export-units",
  "worktree": "D:\\MONKEYHUB_DEV\\workspace\\worktrees\\codex-412-export-units",
  "base_ref": "<约定提交>",
  "contributor": "<实际责任人>",
  "reviewer": null,
  "handoff": null,
  "modules": ["<module id>"],
  "write_scope": ["<窄路径>"],
  "depends_on": []
}
```

```powershell
python tools/devctl.py work
python tools/devctl.py work --json
```

查看一项时写精确 id：`work GH-<issue>`、`work GH-<issue>/<lane>`。这些命令只读已登记的当前 claim、基线、责任与重叠，不联网确认合并，不创建 worktree 或改 registry。

`active` 与 `review` 是当前写入范围声明；`blocked` 不占并发路径。两个当前 claim 声明相同非共享生产路径时，缩窄范围，或把后行工作标为 `blocked`、加入 `depends_on` 并在 `handoff` 写明先后顺序。依赖仍在 registry 中的工作不能标为 active/review。上游合入后核实实际提交、更新基线和依赖，再继续。

一次 claim 的提交顺序：

1. `GH-<n>[/<lane>]: claim …` —— 只改 `work_registry.json`；
2. `GH-<n>[/<lane>]: …` —— 实际修改，只落在该 claim 的 `write_scope` 与 policy 的 `shared_write_scope` 内；
3. `GH-<n>[/<lane>]: release …` —— 从 registry 删除该 claim；按 lane 登记的 Issue 在最后一个 lane 释放时整项删除。

`archcheck --changed <base>` 按每次提交自身的 policy 与 registry 检查；只有释放 claim 的那次提交可以沿用父提交里的 scope，未知或格式不完整的 GH id 不会部分匹配其他 Issue。规则与说明维护同样开 Issue、登记 claim；提交标题以 `P###`、`M###`、`R###` 或 `P000-governance` 开头会报 `RETIRED_WORK_CLAIM`。#358 之前的提交（其 registry 仍是旧 schema）保留当时的读法，一次提交之后不能再退回旧 schema。旧提交里标题只以单个 `(#n)` 结尾、没有其他 Issue 引用和 `GH-` 标记的，按 `GH-<n>` 认领检查（GH-319 分支上有这样的提交）；新提交必须显式写 `GH-<n>`。

已释放的 lane 合并 `main` 时，若 merge 的合并 diff 含共享范围以外的文件，先把该 lane 原样恢复进 registry，再以 `GH-<n>/<lane>: merge origin/main` 为标题合并，最后释放。

#358 之前开出的分支合并 `main` 时用 merge，不要 rebase（rebase 会在旧格式的 claim 提交处冲突）。在同一个 merge 提交里把自己的 registry 条目改成上面的 @3 形状，并删掉分支上残留的 `docs/mapping/planning/GH-<n>-*.md`：`docs/mapping/` 已不在共享范围，放到之后的提交里删会越出 scope。此后的 claim／release 提交标题写 `GH-<n>[/<lane>]: …`。

## 接着读什么

- [`CLA.md`](CLA.md) — 贡献版权/专利授权与双重许可边界。
- [`LICENSING.md`](LICENSING.md) — AGPL 与商业许可说明。
- [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) — 什么放哪、什么不进仓库。
- [`AGENTS.md`](AGENTS.md) — 少量长期规则与项目边界。
- [`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — 环境搭建与首次跑通（队友从第 8 节开始）。
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — 对外协议，客户端能依赖什么。
- [`apps/archflow-studio/README.md`](apps/archflow-studio/README.md) — 项目运行时（Project Runtime）API，以及 MonkeyHub 前端的 `api:check`、`typecheck`、`build`。