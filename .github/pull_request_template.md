## 工作归属

P___ 或 P000-governance — <本次解决的问题和修改后的行为>

<!-- 复用现有 live 工作卡；没有合适归属时才新建。每个提交 subject 写所属 P###，
     未写时 checker 才读取正文。检查采用提交当时的 scope，不追溯规则引入前的历史。
     P000-governance 仅适用于 checker 已列定的规则维护路径和 README.md，不是通用越界许可。
     工作卡和模块 canonical 标签都不代表项目 HEAD 或软件版本已经发布。 -->

## 写入范围

- [ ] 改动落在本次工作卡的 `write_scope` 与 policy 的 `shared_write_scope`，或 `P000-governance` 的有限范围内。
- [ ] 已交接重叠路径，staged diff 只包含本次修改，保留其他 WIP。
- [ ] 若软件归口、公开契约或列出的测试改变，已同步现有 module registry；内部修复不要求改表。

改到的路径:

```
<git diff --name-only <PR-base>...HEAD 的结果，或明确路径清单>
```

## 验证

按本次影响选择检查，只勾实际完成项；未跑或不适用的写明原因。纯文档检查链接、命令和 scoped diff，不要求跑业务套件。

- [ ] 受影响行为测试：<命令与结果>
- [ ] `python tools/archcheck.py` — 当前文件树的静态边界
- [ ] `python tools/archcheck.py --changed <PR-base>` — 已提交源码范围
- [ ] API／DTO 改动：<相关路由检查与 `api:check` 结果>
- [ ] Web 改动：<交互检查、typecheck／build 结果>
- [ ] 文档或手动核验：<实际检查内容与结果>

## 请 reviewer 重点看

- <哪个文件 / 哪个判断最值得质疑>
- <独立功能如何调用现有接口；确实变化的软件契约、写入或校核边界>
- <仍影响使用的限制，以及需要继续的具体任务>
