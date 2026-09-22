# GH-185 — 有范围的项目决定:把"已经定下来的话"留在项目里

**状态:** review（实现完成在 `codex/185-scoped-decisions`,等复核与父会话的收尾提交）
**分支 / worktree / base:** `codex/185-scoped-decisions` / `C:/Users/asus/.codex/worktrees/042c/ARCHFLOW_V4` / `5217609c`
**唯一 live 清单:** `governance/work_registry.json`（本卡 = 该清单里的 GH-185）
**边界:** 本卡只做数据边界。真实 provider 的 observe–judge–revise 工具与 Hub 产品闭环属于 GH-183。

## 1. 先让什么问题消失

既有 Stage 和 StateRecord 已保留参数锁与声明条件。本片补上 hatch、文案反馈和设计 keep 的
原话、解释来源、适用范围与撤销历史,使新的任务上下文可以读取这些判断,无需重放旧对话。

## 2. 修复前的核对

| 发现 | 动作与边界 |
| --- | --- |
| `studio.intent` 已经拥有"一句话 → 记录"的整条路径和 ContextPack | EXTEND:决定挂在它下面,不开新模块、新框架 |
| `studio.board` 已经拥有场景修订链,但只能读最新一版 | 扩它的 `read_board(binding, revision_sha256=None)`,不动场景持久化与导出语义 |
| `DeliberationEpisode` 已有 proposal / producedRun 语义 | 不借用:一条决定不是一次提案裁决 |
| P036 已有固定显式 run 的先例(`studio-board`) | 同样做法:一个固定显式 run `studio-decisions`,不加第二套持久化权威 |

## 3. 已实现的范围

1. **记录种类** `studio-scoped-decision`(`StudioScopedDecision@1`,RUN_REVIEW),登记在 `archflow/project/record_kinds.py`。
   全部修订留在**一个**固定显式 run `studio-decisions` 里,首次显式保存时用 `repository.create_run` 建出来;
   没有独立的 root / index / cache / HEAD。读取只 `load_run` 这一个 run 再按种类 `list_json`,永不枚举全项目 run。
2. **不可变修订链**:`decisionId` 稳定,`previousRevisionRef` 是确切的 P036 ref。竞争 tip、缺父、成环、分叉一律 fail closed
   (`DECISION_CONFLICT`),不按时间戳选胜者;run manifest 损坏时拒绝,而不是读成"这个项目没有决定"。
   supersede / revoke 都是新修订,旧措辞原样保留;`superseded` 是读整条链推出来的,不落第二份状态。
3. **确切来源**:board(历史修订 + 当时**未删除**的元素 id,顺序无关)、document(确切 run/asset/revision/page,
   `revisionRef: null` 不会被悄悄换成生成修订)、design(真实 retained run + `require_actionable` + stateDigest,必要时 Stage)。
   表示层的决定不需要、也不会被要求编一个 Design run。
4. **范围与适用性**:`project` / `stage` / `targets`;`stage` 对着已提交设计历史独立校验;`targets` 只认设计域里记录真的声明过的 ref。
   `scope` 在范围内跨修订继续有效,`exact-source` 只在读同一份来源时有效。
5. **参数绑定**:服务端自己读当前值/单位/基础/锁,不收客户端声称的值。`lock` 只**记录并核验**已有的锁——
   没有锁就拒绝,撤销一条决定也**不会**解锁任何参数。
6. **API**:`POST/GET /api/decisions`、`GET /api/decisions/{id}`、`POST /api/decisions/{id}/revisions`,
   以及既有 `POST /api/intents/context` 上加法式的 `decisionContext` 请求字段与 `scopedDecisions` 顶层返回。
   写用既有 accept 授权,读用既有 read 授权,不新增授权种类,也不给 agent MCP 写权限。
7. **来源声明**:`sourceKind` 与 `messageSource` 都是调用方的**声明**(agent 解释过任何字段就该写 `agent`),
   与边界自己解析出来的 `attribution` 分开保存;两者都不是凭据。

## 4. 明确不做

- 不做通用谓词、学习机制或偏好 DSL,不做排序引擎,不做第二个 contextpack 端点。
- 不为"表示层偏好"写任何几何、Design Stage 或 HEAD。
- 不编造确定性规则:含糊的审美判断保持原话(raw preference)或 `defer`;`defer` 不会作为要求注入。
- 不改 Hub 产品文件、不跑真实 provider、不做基准。

## 5. 验收

- 三个 fixture 家族各自:原样 UTF-8 措辞与消息引用冷启动重开后仍在;board 历史修订在最新版变化后仍可校验;
  伪造/缺失的文档来源被拒;keep/lock 绑到真实参数与真实锁。
- supersede + revoke 的 CAS 与历史保留;链损坏与 run 损坏 fail closed。
- 域 / Stage / target 不匹配的决定不进入下一轮上下文;`scope` 跨来源修订存活而 `exact-source` 变 stale;
  参数值/锁/目标消失都推导为 stale,且不改写已保存的记录。
- 修改关系的一端仍读取与其相交的范围判断;不相关目标排除。全部适用判断返回,没有条数门槛。
- P036 GC 保留决定引用到的自动 run(24 小时后仍在),未被引用的照常回收。
- `apps/archflow-studio/api/tests/test_decisions.py` 与既有 boards / intent-context / collaboration-auth 套件,
  外加 `python tools/archcheck.py`。

## 6. 交接

GH-183 消费 `GET /api/decisions` 与 ContextPack 的 `scopedDecisions`,并负责真实 provider 的保存/撤销工具、
消息来源的实际填写与产品闭环。本卡的线上契约已冻结,不再改字段名。
