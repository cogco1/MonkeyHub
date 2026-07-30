# Claude 修复轮 — 工作日志（2026-07-30）

每项修复：改动内容 → 验证方式 → 结果。基线快照见计划文档。

## F01 — GBK 编码兼容（已完成）

**问题**：`tests/test_devctl.py` 与 `tools/devctl.py` 的 `subprocess.run(text=True)`
未指定编码，继承宿主 locale（中文 Windows = GBK）。devctl 输出的 UTF-8 卡片
文本（如 em-dash）导致父进程读线程 `UnicodeDecodeError`，`stdout` 变 `None`，
`test_context_capsule_is_deterministic_bounded_and_read_only` 报错。

**改动**：
1. `tools/devctl.py main()`：入口处 `reconfigure(encoding="utf-8")` 自身
   stdout/stderr——devctl 输出在任何 locale 下都是确定的 UTF-8。
2. `tools/devctl.py _run_verification_commands()`：子进程解码固定
   `encoding="utf-8", errors="replace"`，并给子进程设 `PYTHONUTF8=1`
  （验证命令是 python 进程，保证其输出确为 UTF-8）。
3. `tests/test_devctl.py run_devctl()`：读取子进程输出固定 `encoding="utf-8"`。

**验证**：清除 `PYTHONUTF8` 后在默认 GBK 环境运行
`python -m unittest discover -s tests -p test_devctl.py` → 13 tests OK。
修复前同环境该文件必现 1 error。

## F02 — 回执构建先于 CAS（审查发现 #7，已完成）

**问题**：`Committer.commit` / `commit_decision_package` 的顺序是
`_apply → _compare_and_swap → _receipt`。`_receipt` 计算 `package.package_id`
时才触发 package 深校验，校验异常会留下"已晋升但无回执"的规范版本，
违反回执绑定完成的根基。

**改动**：两条提交路径均改为 `_apply → _receipt → _compare_and_swap`，
回执构建失败在状态推进前中止。

**回归测试**：`test_receipt_failure_aborts_before_promotion`——patch
`_receipt` 抛错，断言 store 状态保持在 base 不动。

## F03 — 验证回执绑定提交内容（审查发现 #6，已完成）

**问题**：`ValidationReceipt` 只含 `submission_id`/`checked_state`，不含被验
提交的内容摘要。复用同 id/base 但替换 delta 的提交可以借用他人的通过
回执入库（审查已实证把未经门检的 `facts_add` 混入规范状态）。

**改动**：
1. `archflow/submission/model.py`：新增 `CandidateSubmission.content_digest()`
  ——对提交全文（id/base/workspace/intent/delta 全部五类/claims/evidence/
   unresolved）做规范化 JSON SHA-256。
2. `archflow/validation/model.py`：`ValidationReceipt` 新增必填字段
   `submission_digest`（64 位 hex 校验）。
3. `archflow/validation/engine.py`：`validate_submission` 自动盖章。
4. `archflow/commit/committer.py`：`_check_decision_package` 重算并比对，
   不匹配即 `CommitRejected`。
5. 更新 3 处直接构造回执的测试 fixture。

**回归测试**：`test_substituted_delta_cannot_reuse_validation_receipt`——
拿合法回执配替换过 delta 的提交，断言拒绝且状态不动。改动后全量套件
359 tests OK。

## F04 — 承诺门与规范承诺集比对（审查发现 #8，已完成）

**问题**：提交时只检查 monitor 回执的三个布尔值；monitor 跑在调用方
自备的分支状态上，从不与规范状态的承诺集合比对。对空承诺集算出的
"通过"回执可以放行一个仍有未满足 HARD ACTIVE 承诺的晋升。

**改动**：`commit_decision_package` 新增覆盖校验——当前规范状态中状态
属于 {ACCEPTED, ACTIVE, VIOLATED}（与 monitor 的 monitored_statuses 对齐）
的每个承诺，必须出现在 monitor 回执的 `progress` 里，否则拒绝。

**回归测试**：`test_unmonitored_canonical_commitment_blocks_promotion`。

## F05 — HEAD CAS 跨进程文件锁（审查发现 #9，已完成）

**问题**：`compare_and_swap` 的读-校验-替换临界区只有进程内
`threading.RLock` 互斥；两个进程可同时从同一 base CAS 成功，先接受的
晋升被静默挤成孤儿且 `verify()` 不报。

**改动**（`archflow/project/repository.py`）：
1. 新增 `_HeadFileLock`：`<root>/HEAD.lock` 上的 OS 级建议锁
  （Windows `msvcrt.locking` / POSIX `fcntl.flock`），非阻塞尝试 +
   50ms 重试、10s 超时后抛出新异常类型 `ProjectHeadLocked`。
   锁文件不带 `.json` 后缀，完整性扫描不会视为项目记录。
2. `compare_and_swap` 临界区改为 `with self._lock, self._head_lock:`——
   先线程锁后文件锁，同进程线程在 RLock 上序列化，文件句柄单线程持有。
3. `_replace_atomic` 在 `os.replace` 后对目标做 fsync（Windows 需要
   可写句柄，`"r+b"`），降低断电回退风险（顺带修复审查的次要发现）。

**回归测试**：`test_head_cas_is_exclusive_across_processes`——真实子进程
持有锁文件时主进程 CAS 抛 `ProjectHeadLocked` 且 HEAD 不动；子进程释放
后同一 prepared transition 成功。

## F06 — pause 不能洗白已写入候选（审查发现 #5，已完成）

**问题**：`pause` 接受 UNDO_REQUIRED 等已写入状态，`cancel`/`approve`
又接受 PAUSED——先暂停再取消/重批可绕过精确撤销守卫，无凭据地丢弃
未完成的世界撤销义务。

**改动**（`archflow/runtime/player_control.py`，schema @1→@2）：
1. `CandidateControlState` 新增 `paused_from` 字段 + 不变量（仅 PAUSED
   可携带、必须是可恢复状态）+ `effective_status` 属性。
2. `pause` 记录来源（重复暂停保留原始来源）；`cancel`、`approve`、
   `record_exact_undo` 一律按 `effective_status` 判定；从 PAUSED 退出时
   清空 `paused_from`。`record_exact_undo` 现在接受 PAUSED-from-written
  （否则封堵后这类状态将成为死胡同）。

**回归测试**：`test_pause_cannot_launder_written_candidate`——
UNDO_REQUIRED→pause 后 cancel/approve 均被拒、重复暂停不丢来源、
精确补偿仍可从 PAUSED 完成恢复。

## F07 — readiness 刷新对齐状态校验器（审查发现 #4 + 相邻缺陷，已完成）

**问题**：编译器的 `_refresh_obligation_readiness` (1) 求值表不含
`invalidated:*` 引用，与 `value_for_ref` 分歧——失效一个被条件引用的
ref 后，合法分支状态永远无法再编译（构造后继状态时校验器直接抛
ValueError）；(2) 单趟刷新用刷新前的义务状态求值，义务间条件依赖时
与校验器（用最终状态求值）分歧，同样导致合法转移崩溃。

**改动**（`archflow/state/decision_operator.py`）：
1. 刷新调用点移到失效闭包与后继失效集合计算之后，传入
   `invalidated` 集合；
2. 条件求值镜像 `value_for_ref` 语义（先查值表、缺失时回落
   `invalidated:*` 前缀判定，用哨兵区分"值为 None"与"无此键"）；
3. OPEN/BLOCKED 赋值迭代至不动点（上界 len+1 轮），不收敛时抛出
   确定性的 `DecisionCompilationError`；结构性检查（blocker 存在 +
   阻塞边存在）保持原语义，提取到迭代外。

**回归测试**：`test_invalidated_condition_obligation_opens_after_invalidation`。

## F08 — mesh 体素占用做真实有界采样（审查发现 #10，已完成）

**问题**：`_contains` 把 mesh 当 AABB 处理（尽管损失码声称"有界采样"），
`to_observation` 还丢弃损失码、发布 `unknown_count=0`——包围盒过近似
被当作确定占用喂给 P030 硬验证。

**方案权衡**：若只把 mesh 格子标为 unknown，`validate_usability` 会在
`unknown_count>0` 时判全部硬门失败——含细部资产的混合工件将整体
无法验证，破坏混合场景架构。诚实且保架构的修法是实现损失码
声称的采样本身。

**改动**（`archflow/realization/sandbox.py`）：`_contains` 对 mesh 先做
包围盒粗剔除，再用固定通用方向的 Möller–Trumbore 射线奇偶性测试
（确定性、无随机）对实际三角形求点包含；四边形面扇形三角化。
损失码保留（亚分辨率特征与非水密网格仍是近似）。

**回归测试**：`test_mesh_occupancy_samples_actual_mesh_not_bounding_box`
——四面体 AABB 内但四面体外的采样点不再算占用。

## F09 — check-scope 去恒真化（审查发现 #11，已完成）

**问题**：无参 `check-scope` 拿 write_scope 和它自己比（恒真必过），
归档卡将其输出当作"范围检查通过"证据；完成命令从不调用范围检查。

**改动**：
1. `tools/devctl.py`：无参 `check-scope` 现在直接报错退出（exit 2），
   错误信息言明"write_scope 自比不构成验证"。
2. `governance/work_registry.json`：唯一使用无参形式的 P001 验证命令
   改为显式路径清单；`render-map` 重渲染保持文档一致。
3. **残余缺口（记录在案）**：完成路径仍不核对实际改动文件——仓库
   不是 git 库，无法枚举真实触碰的路径。根治需要 git init 或文件清单
   追踪，属独立工作项。

**回归测试**：`test_check_scope_without_paths_is_an_error_not_a_pass`。

## F10 — sandbox_gold 两条必崩路径 + 端到端测试（审查发现 #1 #2，已完成）

**问题**：接受路径调用不存在的 `ValidationReceipt.to_dict()`/
`UsabilityReceipt.to_dict()`/`readiness.receipt_id` 成员（模块里本就有
正确的 `_validation_dict`/`_receipt_dict` 辅助函数，作者忘了用）；
`reload_sandbox_gold` 读取从未序列化的 `program_digest` 键。

**改动**（`archflow/runtime/sandbox_gold.py`）：
1. accepted-archive 块改用模块自己的序列化辅助函数；readiness 证据
   引用改为其 to_dict 的摘要（`CandidatePromotionReadiness` 无
   receipt_id 字段）。
2. `_validation_dict` 补上 F03 新增的 `submission_digest` 字段。
3. reload 改为用 `digest_value(payload["geometry_program"])` **重算**
   程序摘要与实现回执比对——比读取自 declared 键更强的绑定。

**端到端测试**：新增 `tests/integration/test_sandbox_gold.py`——脚本化
假模型提供者按 schema 返回概念/修订提案，驱动完整 Gold 管线：
概念→硬门拒绝→修订→几何编译→确定性实现→体素派生→五视图渲染→
硬验证→预授权审批→承诺监控→晋升就绪→归档→CAS 晋升→verify→
reload→重启后再 reload。**这是该模块首次被执行**，一次通过。

## 第一轮收尾验证

- 全量套件：366 tests OK（357 基线 + 9 新回归），双环境通过。
- `tools/archcheck.py`：ARCHITECTURE PASS。
- 全部改动 diff 交 10 智能体对抗性复审（4 分区审查 + 6 项逐条验证）。

## 复审返工（第二轮）

复审确认了 6 项缺陷（0 项被驳倒），全部处理如下：

### R1 — F04 门只比 id，同 id 弱化替身可通过（确认，中危）

复审员用可执行复现证明：monitor 跑在"同 id 但 strength=PREFERENCE"的
替身承诺上时所有缺证发现降级为 ADVISORY、`passed=True`，而 progress 的
id 集合恰好覆盖规范集合——HARD 承诺零证据晋升。

**修复**：`CommitmentProgress` 新增必填字段 `commitment_digest`
（monitor 实际评估的承诺内容摘要，新导出
`commitment_content_digest()`）；committer 覆盖检查升级为三重判定：
id 存在 + 内容摘要与规范承诺一致 + outcome 不是 EVIDENCE_BLOCKED。
monitored 状态集提为 `MONITORED_COMMITMENT_STATUSES` 模块常量，
committer 直接导入（消除双定义漂移，复审低危项一并解决）。
**回归测试**：`test_lookalike_commitment_content_cannot_cover_canonical`
——断言 monitor 本身通过、committer 拒绝、状态不动。

### R2 — initialize() 不持 OS 锁写 HEAD，重复初始化 TOCTOU 可回退已晋升 HEAD（确认，中危）

**修复**：`initialize()` 的清单存在性检查与 HEAD 写入移入与
`compare_and_swap` 相同的 `_HeadFileLock` 临界区；HEAD 已存在时
直接 `ProjectAlreadyExists`（双保险）。

### R3 — post-replace fsync 使 CAS 在交换已生效后抛错（确认，中危，本轮引入）

F05 顺手加的 HEAD 落盘刷新会在 `os.replace` 已可见后失败抛错，破坏
调用方"异常=未晋升"的契约（且以可写句柄打开 HEAD 增加损坏面）。
**修复**：回退该 fsync，并留注释说明取舍——契约完整性优先于
重命名元数据的额外落盘（临时文件本身的 fsync 保留）。

### R4 — F03 审计链仍不含内容摘要（确认，中危）

**修复**：`submission_digest` 纳入验证回执 id 的摘要负载
（`engine._receipt_id`）、提交回执 id 的摘要负载，并作为
`CommitReceipt.submission_digest` 新字段留档。

### R5 — mesh 采样对模型控制的面数无上界（确认，中危）

**修复**：`SandboxAssetPayload` 增加显式上界
（MAX_VERTICES/MAX_FACES = 20,000），越界即类型化拒绝。

### R6 — artifacts-only 不变量靠摘要计算的副作用兜底（确认，低危）

**修复**：`commit_decision_package` 在 `_apply` 前显式检查 P024
"delta 只许携带 artifacts"契约，违反即 `CommitRejected`（原先逃逸为
`CandidateAssemblyError` 且时机依赖摘要计算）。

### 复审确认但记录不修（低危 / 平台外 / 需独立工作项）

- 传统 `commit()` 路径无 F04 门——该路径被 `goal is None` 检查限定为
  走査骨架兼容用途，生产必须走 package 路径；如未来开放需补齐。
- POSIX 重命名持久化需父目录 fsync——本仓库当前仅在 Windows 运行，
  POSIX 分支为静态审阅代码。
- 射线奇偶性在共享边/顶点上的双计数——损失码已声明有界采样语义。
- readiness 不动点对存在多稳定解的 delta 保守拒绝——确定性优先。
- `paused_from` 在直接构造层面可伪造——与全库既有"记录可构造"
  威胁模型一致，防线在转移函数层。
- F09 的 P001 注册表命令显式路径仍等于 write_scope（自指）；根治
  需要 git 追踪实际改动文件，见"建议后续工作"。

## 最终验证

- 全量套件：**367 tests OK**（357 基线 + 10 新回归，2 个环境门控
  跳过），`PYTHONUTF8=1` 与默认 GBK 双环境通过。
- `tools/archcheck.py`：ARCHITECTURE PASS (99 files)。
