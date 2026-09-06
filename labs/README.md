# labs/ — 兴趣驱动的探索

`labs/<名字>/` 是每个人自己的试验田：想验证的算法、想试的库、还没想清楚归谁的原型都放这里，不需要卡、不需要注册表 owner、不进 CI 的脊柱套件。

**唯一的规则:** lab 可以 `import archflow.*`;`archflow/`、`tools/`、`apps/`、`tests/` 任何一个都不许 `import labs`。
`tools/archcheck.py` 用和 archive 同一套 `forbidden_layer_imports` 机制拦这条,越界报 `LAYER_AUTHORITY_VIOLATION`。
反过来说,lab 里的代码不受注册表 owner、写入点、重复权威这些检查约束(policy 的 `import_only_source_roots` 把 `labs` 划出去了),你在自己的目录里怎么写都行。

**毕业进脊柱的四件事,同一个 PR 里做完:**

1. 先有一张卡(`governance/work_registry.json` 一项 + `docs/mapping/planning/P###-*.md` 一张),写清目标、验收、`write_scope`。
2. 一个 owner:`governance/module_registry.json` 里一条 `owns` 条目,不与已有 owner 重复。
3. 代码落到脊柱的 owner 路径下,带上测试进 `tests/`。
4. `labs/` 下的那份副本在同一个 PR 里删掉——不留平行实现。

lab 里的数据、模型、导出物不进仓库(见 `docs/REPO_LAYOUT.md` 第 4 节);`.gitignore` 之外的大文件自己收进工作区。
