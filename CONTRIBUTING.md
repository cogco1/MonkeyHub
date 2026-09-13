# 协作流程

[`AGENTS.md`](AGENTS.md) 保留长期规则；本文说明一次任务如何交接和集成。
`module_registry` 记录软件归口与公开契约，`work_registry` 记录未完成任务及源码范围，
`architecture_policy` 配置静态检查。模块 owner 是软件职责，可以包含多个实现文件，不是个人姓名；工作卡不是发布证明。

1. **先取基线，再查任务与 owner。** `git fetch origin main` 后记录约定的提交（如 `git rev-parse origin/main`），读 Issue，再运行 `python tools/devctl.py work`、`work <卡/lane>` 和 `module <目标>`。用返回的精确 module id 查契约、真实调用方及相关测试，确认当前路径重叠后再改代码。
2. **一个任务一个 lane、短分支和独立 worktree。** 在现有 live 卡的 `lanes` 下登记本次任务；没有合适卡时才新建。新 worktree 使用 `python tools/workspace.py create --branch codex/<简短名称> --base <约定提交>`；首次根目录配置见[开发环境指南](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#开发目录只配置一次)。继续任务复用原检出，不共用脏 worktree，不吸收别人的 WIP。
3. **按 lane 的窄路径修改。** 开工前写明本次 `write_scope`；lane 范围和所属卡范围共同约束提交，policy 的 `shared_write_scope` 仍可共享，不是运行权限。共享测试、生成视图及治理文件按 policy 处理，不自动视为生产路径冲突；共享文件只改本任务条目或段落。有 lane 的提交 subject 写 `P###/lane`，例如 `P115/team-lanes`；无 lane 的旧卡继续写 `P###`。
4. **给独立功能合适的位置。** 先查现有公开函数和调用方；新的分析或出图算法可以有独立目录或外部包，通过函数、CLI、API 或 adapter 接入。不要强迫每个功能改 core 或塞进已有大文件；不要复制已有状态、持久化或发布权威。实验与接入方式见 [`labs/README.md`](labs/README.md) 和指南第 7 节。
5. **按数据用途保存。** 活跃项目使用显式外部项目根，持久数据经 `archflow.project` 的现有接口保存；只有明确晋升的输入和证据进入 `probes/`。用户设置、临时文件和 adapter workspace 沿各自已有边界。确实新增持久记录或受检查的写入点时，更新现有 kind 表或 policy，不为普通内部函数新增登记。
6. **共享接口先落主线。** 已有契约足够时各 lane 独立实现；不足时由现有 owner 的小型上游 PR 补齐，合入 `main` 后相关分支更新基线，不在后端分支复制接口。软件归口、公开契约或列出的测试改变时同步 module registry；内部修复不用改表。DTO 改动后运行 `api:generate` 和 `api:check`，实际对外协议变化同步 [`PROTOCOL.md`](docs/PROTOCOL.md)。前端文案沿用中英文同键表。
7. **按影响验证。** 选择受影响的行为测试、类型检查或构建；纯文档检查命令、链接、生成地图和 scoped diff。`archcheck` 检查当前静态边界，`--changed` 检查已提交范围；重复能力检查只覆盖相同声明和部分代码复制，行为正确性与语义重复仍需测试和 review。
8. **一个 lane 一个 PR。** 只 `git add -- <明确文件>`，核对 staged diff，通过相关 CI 并交独立 reviewer 后集成。PR 写清问题、行为、lane/base、交接、重叠、实际检查和剩余依赖。不直接 push `main`，不重写已推送历史；只在已落地的相关依赖或集成需要时同步，不做每日机械 rebase。完成验收与合并后移除该 live lane；整张卡的剩余验收未完成时保留卡片。

## 登记与查看并行任务

[`governance/work_registry.json`](governance/work_registry.json) 的卡片可带 `lanes` 数组。每个 lane 填写：

| 字段 | 填什么 |
| --- | --- |
| `id`、`issue` | 卡内唯一短名、Issue 或约定工作项，例如 `team-lanes`、`#13` |
| `branch`、`worktree`、`base_ref` | 本任务分支、实际独立检出、约定基线；优先记录准确提交 |
| `contributor`、`reviewer`、`handoff` | 实际责任人、审查人、交接对象与顺序；未指定 reviewer/handoff 写 `null`，不代填人名 |
| `modules`、`write_scope` | 现有 module id 与本次确实要改的窄路径；模块 owner 仍是软件职责 |
| `depends_on` | 需先完成的现有卡或 `P###/lane` 引用；没有依赖时为空数组 |
| `status`、`blocked_reason` | `planned` / `active` / `review` / `blocked` / `done`；blocked 必须说明具体缺项 |

`active` / `review` 必须有实际 `branch`、`worktree`、`base_ref` 和 `contributor`；planned/blocked 尚未分配的这些字段可写 `null`，不以占位人名假装已认领。

```powershell
python tools/devctl.py work
python tools/devctl.py work P115
python tools/devctl.py work P115/team-lanes
python tools/devctl.py work --json
```

这些命令只读已登记的任务、基线、责任与重叠，不联网确认合并，不创建 worktree 或改 registry。没有 `lanes` 的旧卡继续沿用卡级规则。

`active` 与 `review` 是当前写入范围声明；`planned`、`blocked`、`done` 不占并发路径。两个当前 lane 声明相同非共享生产路径时，缩窄范围，或把后行 lane 标为 `blocked`、加入 `depends_on` 并在 `handoff` 写明先后顺序。依赖未完成的 lane 不能标为 active/review。上游合入后核实实际提交，更新相关 lane 的基线和依赖，再继续；完成的 lane 移除时一并清理已满足的引用。

`archcheck --changed <base>` 按每次提交当时的 policy、工作卡和 lane 检查；规则引入前不追溯，无 lane 的旧卡沿用旧规则。只关闭或移除 lane/卡片的那次提交可沿用 first-parent 的有效范围。此后删除或损坏配置会报错。
仅规则与说明维护可明确写 `P000-governance`：允许已列定的 checker、policy、CI、PR 模板、规则文档与 `README.md`，不允许业务源码、任意脚本或所有 Markdown。准确路径由 [`tools/archcheck.py`](tools/archcheck.py) 定义。

## 接着读什么

- [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) — 什么放哪、什么不进仓库。
- [`AGENTS.md`](AGENTS.md) — 少量长期规则与项目边界。
- [`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — 环境搭建与首次跑通(队友从第 8 节开始)。
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — 对外协议,客户端能依赖什么。
- [`apps/archflow-studio/README.md`](apps/archflow-studio/README.md) — Studio 的 api / web 与 `api:check`、`typecheck`、`build`。
- [`docs/mapping/planning/`](docs/mapping/planning/) — 可以认领的卡(`INDEX.md` 列出 ready 的)。
