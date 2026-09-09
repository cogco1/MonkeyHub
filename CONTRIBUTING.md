# 怎么在这个仓库里干活 — 十条

1. **一人一卡一分支。** 动手前先在 `governance/work_registry.json` 认领一张卡(没有就先写一张 `docs/mapping/planning/P###-*.md`),从 `main` 开 `p1xx-<slug>` 分支,只走 PR 合入,CI 全绿加一个人 review 才合;每天 `git rebase main` 一次,别让分支飘走。
2. **卡上的 `write_scope` 是硬边界。** 你的提交只能改这张卡列出的路径,加上共享账本(`tests/`、`docs/mapping/`、两张 registry);越界 CI 直接红(`archcheck --changed` 报 `SCOPE_VIOLATION`)。卡号 `P###` 写在每个提交的 subject 里(subject 没写才去 body 里找),`archcheck` 靠它认这次提交归谁。
3. **兴趣探索进 `labs/`。** `labs/<名字>/` 随便写、可以 `import archflow.*`;但 `archflow/`、`tools/`、`apps/`、`tests/` 谁都不许 `import labs`,越界 `archcheck` 报 `LAYER_AUTHORITY_VIOLATION`。毕业规则见 [`labs/README.md`](labs/README.md)。
4. **新模块先进注册表,一个能力一个 owner。** 在 `governance/module_registry.json` 写 `owns` / `public_api` / `tests` 再写代码;同一个能力出现第二个实现,`archcheck` 会报 `REGISTRY_DUPLICATE_OWNER` 或 `DUPLICATE_OWNED_FUNCTION`。默认扩展已有 owner,新建要写清为什么没有 owner 合适。
5. **新记录种类先登记 `record_kinds.py`。** `archflow/project/record_kinds.py` 是 kind 表的唯一入口,没登记的 kind 项目仓库拒收;记录里的语义字段只认 `archflow/semantics/` 里的 `role.*` / `condition.*`。
6. **新写入点先登记 policy。** 磁盘写入必须走 `archflow.project` 的端口;确实要新开一个写入点,先在 `governance/architecture_policy.json` 的 `allowed_write_sites` 写下 path / function / operations / kind / owner / reason,否则 `archcheck` 报 `UNOWNED_FILESYSTEM_WRITE`。
7. **改了 DTO 就重生成客户端。** 在 `apps/archflow-studio/web` 跑 `npm run api:generate`,再用 `npm run api:check` 确认没漂移;协议上真的加/改了资源或字段,同一个 PR 更新 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)。
8. **前端字符串两张同键表。** 客户端自己的文案写进 `src/i18n/messages.en.ts` 和 `messages.zh-CN.ts`,两张表键必须一一对应,少一个键 typecheck 就红;代码、路径、哈希、协议串不翻译。
9. **设计项目数据不进仓库。** 每个人用自己的 WIP 项目根(外部 `workspace/projects/<project_id>/`),记录、run、导出、截图都留在那里;仓库里只放会被 import、被测试跑、被 CI 检查或被注册表引用的东西。
10. **提交守规矩。** 只 `git add` 明确的文件路径,不用 `git add -A` / `git add .`;不改已推送的历史(不 `--amend`、不 `push --force`),不直接 push `main`。

写入范围检查使用每次提交当时的 policy 和工作卡；规则引入前的提交不追溯检查，结束工作卡的那次提交仍按父提交中该卡的有效范围核验。此后删除或损坏规则配置会报错，不能借此关闭检查。

不涉及能力实现的规则及说明维护可明确写 `P000-governance`：仅限检查器、架构 policy、CI、PR 模板、现有规则文档，以及根目录或包内的 `README.md`。这不允许改业务源码、其他 Markdown 或任意工具脚本；这些改动仍须由对应工作卡声明范围。

## 接着读什么

- [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) — 什么放哪、什么不进仓库。
- [`AGENTS.md`](AGENTS.md) — 架构纪律:值与回执、持久化权威、动手前的五步。
- [`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — 环境搭建与首次跑通(队友从第 8 节开始)。
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — 对外协议,客户端能依赖什么。
- [`apps/archflow-studio/README.md`](apps/archflow-studio/README.md) — Studio 的 api / web 与 `api:check`、`typecheck`、`build`。
- [`docs/mapping/planning/`](docs/mapping/planning/) — 可以认领的卡(`INDEX.md` 列出 ready 的)。
