## 卡号

P___ — <一句话说这张卡这次推进了什么>

<!-- 把这个 P### 写进每个提交的 subject;subject 没写才会去 body 里找,而 body 里提到的
     别的卡号会被当成这次提交的归属。archcheck --changed 靠它认写入范围。
     治理类改动(没有卡)写 P000-governance,并说明为什么不需要卡。
     P000 只允许既有规则维护路径与 README.md，不放开业务源码或任意脚本。
     检查读取提交当时的 policy/registry；规则引入前不追溯，退役卡的最后提交读其父提交范围。 -->

## 写入范围

- [ ] 改动只落在这张卡的 `write_scope` 里,加上共享账本(`tests/`、`docs/mapping/`、`governance/work_registry.json`、`governance/module_registry.json`)。
- [ ] 没有动别人卡的路径;需要动就先改卡、或者拆一张新卡。

改到的路径:

```
<git diff --name-only main...HEAD 的结果,或手写清单>
```

## 验证

勾选实际跑过的;没跑的写"未跑"和原因,别默认勾。

- [ ] `py -3.12 tools/archcheck.py` — 架构防火墙
- [ ] `py -3.12 tools/archcheck.py --changed main` — 写入范围
- [ ] `py -3.12 -m pytest tests -q` — 脊柱套件(___ passed)
- [ ] `PYTHONPATH=<repo> py -3.12 -m pytest apps/archflow-studio/api/tests -q` — Studio API(___ passed)
- [ ] `npm run api:check` / `npm run typecheck` / `npm run build`(在 `apps/archflow-studio/web`)
- [ ] 手动核验:<在真实项目上看到的行为,或写"无">

## 请 reviewer 重点看

- <哪个文件 / 哪个判断最值得质疑>
- <有没有引入新的 owner、新的记录种类、新的写入点、新的协议字段>
- <哪些是这次故意没做的(留给哪张卡)>
