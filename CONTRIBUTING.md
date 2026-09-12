# 协作流程

[`AGENTS.md`](AGENTS.md) 保留长期规则；本文说明一次任务如何交接和集成。
`module_registry` 记录软件归口与公开契约，`work_registry` 记录未完成任务及源码范围，
`architecture_policy` 配置静态检查。模块 owner 是软件职责，可以包含多个实现文件，不是个人姓名；工作卡不是发布证明。

1. **使用独立检出和短分支。** 新 worktree 使用 `python tools/workspace.py create --branch codex/<简短名称>`，从约定基线开始时追加 `--base <ref>`；首次根目录配置见[开发环境指南](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#开发目录只配置一次)。继续任务时复用原检出，保留维护者主检出的 WIP 和已有明确约定；需要同步时再按实际情况合并或 rebase，不做每日强制操作。
2. **复用现有工作卡。** 先找本次任务所属的 live 卡，约定结果、验收和 `write_scope`。只有没有合适归属时才新建卡；完成后从 live 清单移除，成果以提交、PR 和实际交付状态说明。
3. **按明确源码范围修改。** 卡片范围加上 policy 的 `shared_write_scope` 是提交边界，不是运行权限。共享范围现为 `docs/mapping/`、两张 registry、`tests/`、API tests 和 `docs/PROTOCOL.md`。涉及他人负责的路径，先交接本次修改范围。提交 subject 写所属 `P###`，未写时 checker 才读取正文。
4. **给独立功能合适的位置。** 先查现有公开函数和调用方；新的分析或出图算法可以有独立目录或外部包，通过函数、CLI、API 或 adapter 接入。不要强迫每个功能改 core 或塞进已有大文件；不要复制已有状态、持久化或发布权威。实验与接入方式见 [`labs/README.md`](labs/README.md) 和指南第 7 节。
5. **按数据用途保存。** 活跃项目使用显式外部项目根，持久数据经 `archflow.project` 的现有接口保存；只有明确晋升的输入和证据进入 `probes/`。用户设置、临时文件和 adapter workspace 沿各自已有边界。确实新增持久记录或受检查的写入点时，更新现有 kind 表或 policy，不为普通内部函数新增登记。
6. **接口变化才同步契约。** 软件归口、公开契约或列出的测试改变时，同步 module registry；内部修复不用改表。DTO 改动后运行 `api:generate` 和 `api:check`，实际对外协议变化同步 [`PROTOCOL.md`](docs/PROTOCOL.md)。前端文案沿用中英文同键表。
7. **按影响验证。** 选择受影响的行为测试、类型检查或构建；纯文档检查命令、链接、生成地图和 scoped diff。`archcheck` 检查当前静态边界，`--changed` 检查已提交范围；重复能力检查只覆盖相同声明和部分代码复制，行为正确性与语义重复仍需测试和 review。
8. **明确暂存，再发 PR。** 只 `git add -- <明确文件>`，核对 staged diff，不收录他人 WIP。通过相关 CI 并由另一人 review 后集成；不直接 push `main`，不重写已推送历史。PR 写清问题、修改后行为、实际检查和仍影响使用的限制。

`archcheck --changed <base>` 按每次提交当时的 policy 和工作卡检查；规则引入前不追溯，关闭卡片的那次提交可沿用父提交的有效范围。此后删除或损坏配置会报错。
仅规则与说明维护可明确写 `P000-governance`：允许已列定的 checker、policy、CI、PR 模板、规则文档与 `README.md`，不允许业务源码、任意脚本或所有 Markdown。准确路径由 [`tools/archcheck.py`](tools/archcheck.py) 定义。

## 接着读什么

- [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) — 什么放哪、什么不进仓库。
- [`AGENTS.md`](AGENTS.md) — 少量长期规则与项目边界。
- [`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — 环境搭建与首次跑通(队友从第 8 节开始)。
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — 对外协议,客户端能依赖什么。
- [`apps/archflow-studio/README.md`](apps/archflow-studio/README.md) — Studio 的 api / web 与 `api:check`、`typecheck`、`build`。
- [`docs/mapping/planning/`](docs/mapping/planning/) — 可以认领的卡(`INDEX.md` 列出 ready 的)。
