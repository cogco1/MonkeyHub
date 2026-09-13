# MonkeyMonitor

独立的工程用量与预算工具，与 `monkeyarch/`、`monkeydiagram/` 同级。
可以单独启动，无额外 Python／Node 依赖。Studio 只负责把真实调用结果交给它。

## 已可使用

- 读取显式指定的 Codex 会话，统计实际报告的输入、缓存、输出及推理用量。重复累计快照不重复计入；计数重置或历史断点会显示提示。
- Studio 的 Codex／Anthropic 意图调用返回 token 计数；成功、澄清、输出无效及 provider 失败均可记录。没有用量的调用保留为未计量。
- 独立页面沿用 Monkey 系列深浅色、字体、顶栏与控件。首页先比较调用消耗，默认十条；选中调用后查看明细与估价，修改用量和费率时再展开参数。
- 通用 `Algorithm.choose(context)` 接口及 `FirstAvailablePolicy` 基线已实现。接口返回建议，宿主仍负责执行、预算扣减与结果验收。
- Studio 候选生成、几何导出、模型加载及 Stage 保存按实际操作分段计时；可以按项目、运行与确切来源查看。没有模型调用的操作不产生 token 用量。

界面采用 [NN/g 的渐进呈现建议](https://www.nngroup.com/articles/progressive-disclosure/)：
常用比较操作先显示，完整参数按需展开。算法接入说明留在本文，首页不显示开发接口。
首页、调用列表和按模型汇总并列展示四项 benchmark：缓存输入、未缓存输入、输出、等待时间。
排序只改变调用顺序，不隐藏其他指标；总输入保留在旁注及明细中。缓存输入是总输入中的缓存读取，
未缓存输入为总输入减缓存读取（包括新写入缓存的输入），输出已含推理 token，均不重复相加。
缓存命中率按输入与缓存均已知的调用加权计算，不取各调用命中百分比的平均值。
缺失计数保留未知，部分记录只显示已知小计及覆盖次数。等待时间采用已记录模型请求往返的中位数（P50），
不是纯推理时间；不拿工具执行、客户端等待或代理整轮时间补齐旧 Codex 调用的缺失计时。
这些是定位调用的指标，不直接等于费用或自动优化结论。

## 启动

从源码仓根目录运行，Python 3.12 以上：

```powershell
python -m monkeymonitor serve
```

打开 `http://127.0.0.1:8788`。未指定数据源时显示空列表，计价器仍可使用。

从新版 Hub 打开的 Monitor 会自动读取该 Hub 已绑定的 Codex 会话用量，包括已归档会话。
Hub 仅提供项目与会话 ID；Monitor 在 `CODEX_HOME`（默认 `~/.codex`）的只读索引中精确定位日志，
核对会话身份后沿用现有数值解析器，不按工作目录或最近任务猜测项目，也不读取 Hub 聊天正文。
索引尚未出现、会话身份不符或 Hub 不可达时会显示来源提示，已有诊断和手选来源仍可查看。
这条自动连接由 Hub 的 `--codex-bindings-url` 启动参数提供，独立启动仍使用下方的显式来源。

读取一个明确的 Codex JSONL 会话；子代理需要分别传入自己的文件：

```powershell
python -m monkeymonitor serve --codex-session 'C:/path/to/rollout.jsonl'
python -m monkeymonitor report --codex-session 'C:/path/to/rollout.jsonl'
```

`report` 向标准输出返回统计元数据，不写文件。不扫描其他任务，不导出提示词、回答、工具内容或凭据。
选中的文件每次刷新重新读取，因此可跟随正在增长的会话。它不能凭 token_count 把开发调用细分为“查代码／推理／工具”，也不能从累计事件可靠反推模型调用耗时。

页面的“Codex 来源”折叠区可逐行填写明确的父任务及子代理 JSONL 绝对路径，应用后立即刷新。
重复路径与同一会话的重复快照不会重复统计；清空可停止读取。此选择只在当前 Monitor 进程内保留，
重启后通过同一区域重新选择，或使用多个 `--codex-session` 参数。不会搜索其他任务或自动发现子代理。
父子关系仅来自会话元数据；缺失关系保持未知。无法核实旧式继承历史的归属时显示提示，不能据此宣称整个任务已完整计量。

让下一次启动的 Studio 记录用量，并让 Monitor 读取同一目录：

```powershell
$env:MONKEYMONITOR_DATA_DIR = Join-Path $env:LOCALAPPDATA 'MonkeyMonitor/studio'
# 在这个环境中使用原有 Studio 启动命令
python -m monkeymonitor serve --data-dir $env:MONKEYMONITOR_DATA_DIR
```

目录必须由运行者明确提供，位于项目文档之外；`usage.jsonl` 属于 MonkeyMonitor 的非正式诊断数据。
每个目录由一个 Studio 进程追加写入，Monitor 只读。创建应用时不触碰文件；首次记录才创建目录。
不设置变量则保持原启动行为。已运行的 Studio 需要下次启动才会采用设置，历史缺失的 token 无法补算。
源文件直接运行即可；完整发行包也包含页面和费率预设。

## 时间口径

| 页面操作 | 起止与包含范围 | 关联与限制 |
| --- | --- | --- |
| 一次修改 | 提交修改到候选模型加载完成 | `interaction`；显示总历时、实际等待与返回提案到点击生成的间隔。没有客户端根记录时总历时未知 |
| 模型请求 | 实际 provider 调用开始到返回或失败 | 不包括本地提示准备和答案解析；是请求往返，含服务及传输等待，纯推理时间仍未知 |
| 执行前排队 | 候选提交到 worker 进入 | 独立等待段，不并入候选执行计时；与客户端候选等待重叠 |
| 候选生成 | worker 开始执行至返回或失败；包含编译、几何和该候选的保存 | 不包含排队和人工查看；关联项目、候选 run、输入来源与已有模型调用 |
| 几何导出 | 实际导出路径开始至完成、失败或取消 | 展开 OCCT 形体复用检查、几何、STEP 写出/回读、tessellation、预览写出/回读；不能再与候选总时长相加 |
| 模型加载 | 浏览器开始下载至视口加载方法完成 | `client_wait`；包含下载、解析和提交视口，受客户端性能影响，不是服务器或模型耗时；不等于浏览器首帧呈现时间 |
| 图纸生成 | 明确请求立面后，源加载、全局可见线计算、SVG、PNG、保存与注册 | 命中精确已有图纸时不报告未执行的投影阶段；改模型和保存 Stage 本身不自动出图 |
| Stage 保存 | 保存接口开始至返回、拒绝或失败 | `service`；只含保存请求，不含用户看模型、决定接受的停留时间 |
| Codex 任务轮次 | 原生起止字段，或明确配对的 task-start/complete/abort 边界 | `agent_turn`，包含工具执行和等待；没有完整边界则保留未知，不从 token 行间距推算 |

模型请求耗时中位数仅使用明确的 `model_call` 计时。意图编译是请求的服务父项，不重复记录 token；
未提供实际调用边界的旧编译器只能保留模型用量，调用耗时未知。Token 数只在原始调用记录汇总一次，
任务分组、父子分组和操作关联不会创建第二份计数。旧日志仍可读，缺少新时间口径时显示未知。
运行中与最终操作使用同一事件 ID；异常或取消保留实际终止状态。监控写入失败不会重试模型、导出或保存。
浏览器在 `operation-diagnostics` 可用时按单次操作上报；旧服务的 `operation-timing` 加载记录仍兼容。
关闭页面或断网可能缺失最后一条记录。父子区间与并行阶段会重叠，不能相加还原总历时。

展开阶段可检查已存在的输入摘要、实际对象名单、执行路径、缓存比较字段及引用。
OCCT 可以只复用形体而仍重新写出和检查文件；tessellation 的内核内部缓存未观测时显示未知。
MonkeyDiagram 的输入名单参与全局遮挡计算，输出名单说明实际画出了谁，不把它冒充仅修改对象的重算差量。

重复判断只比较当前选中日志中已记录的输入身份：相同模型请求、相同几何/图纸计算或相同资源请求分别呈现。
已有缓存命中单独显示为复用；相同 URL 的候选轮询不被当作重复工作。输入相同仍需验证旧结果的绑定、
缓存可用性和 provider 规则，不能直接断言本来可以免掉这次请求。没有输入身份的旧行保持未知。

## 计价口径

输入总量包含普通输入、缓存读取与缓存写入；1 小时写入是全部写入的子集。
输出总量已经包含推理 token，不能再加一次。

```text
普通输入 = 输入总量 - 缓存读取 - 全部缓存写入
普通缓存写入 = 全部缓存写入 - 1 小时缓存写入
估价 = Σ(每个互不重叠的 token 桶 × 对应每百万 token 单价) / 1,000,000
```

金额使用 Decimal。某个必要桶或费率未知时，总价保持未知，同时展示已知部分的小计。
没有费率的模型不自动映射到“相近型号”。预设仅供明确选择，不能自动判定上下文档位、服务层级或账号优惠。
四个 Standard 预设来自 [OpenAI 官方定价](https://developers.openai.com/api/docs/pricing)，于 2026-09-09 核对；实际使用前可按当前账户与官网修改。
OpenAI 的普通缓存写入价格与 Anthropic 的 5 分钟／1 小时写入档位应按 provider 分别填写。

Codex 会话中换算出来的金额是 API 等价估算，不是订阅扣费或剩余额度。
通过 Studio 调用 Codex 的鉴权／付费方式不能由模型名确定，因此记录为 `unknown`。
估算不包含工具调用费、存储、税费、折扣、区域与优先处理附加费，也不代替账单。

## 算法接口与位置

```text
工作流提供允许动作、候选与已有检查结果
  → evaluator 提供质量／约束／不确定性观测
  → MonkeyMonitor 计价、用量与预算信息
  → Algorithm.choose(SelectionContext) 返回一个动作或停止
  → 宿主执行动作，回填实际用量和结果，进入下一轮
```

| 方法 | 接入位置 | 边界 |
| --- | --- | --- |
| evaluator | 工作流已有检查与评价的调用方，结果进入 observations | 几何正确性与设计判断仍归对应 owner；Monitor 不复制 evaluator |
| OCBA | 已固定候选的重复评估／采样分配策略 | 需要各候选样本均值、方差与成本；尚未实现 |
| MCTS | 多步修改的搜索策略 | 搜索节点与可执行变更由工作流提供；需要真实的状态转移与评价；尚未实现 |
| Pareto | 多目标观测的候选筛选 | 保留冲突目标，不假定唯一综合分；尚未实现 |
| AHP | 明确给定偏好的排序策略 | 权重与成对判断需要可追溯的作者输入；尚未实现 |

接口使用方法：

```python
from monkeymonitor.algorithms import (
    Action, Budget, FirstAvailablePolicy, SelectionContext, choose_action,
)

context = SelectionContext(
    available_actions=(
        Action("reuse-preview", estimated_cost_usd="0", estimated_tokens=0, estimated_time_ms=50),
        Action("generate-candidate", estimated_cost_usd="0.08", estimated_tokens=4000, estimated_time_ms=20000),
    ),
    remaining_budget=Budget(cost_usd="0.10", tokens=5000, time_ms=30000),
    observations={"candidate-a": {"quality": 0.7, "uncertainty": 0.2}},
    objectives={"quality": "maximize", "latency": "minimize"},
)
decision = choose_action(FirstAvailablePolicy(), context)
```

基线按调用方优先顺序选择估算在预算内的动作；未知估算不能通过已声明的预算上限。
这是单步建议校验，尚不是并发预算预留、真实用量强制上限或自动调度器。
第三方策略只能返回调用方允许的动作。不能因此跳过建筑验证、接受设计或写入正式 HEAD。

## 优化实施顺序

1. **先找大头。** 用当前采集器覆盖真实 Studio 请求和明确的开发会话；分别检查输入、缓存命中、输出、失败率及分段耗时。正式发布目前没有独立计时。
2. **缩小模型输入。** 在现有 intent 编译器中复用稳定规则前缀，把状态上下文收敛到选择对象、受影响依赖及完成本次修改需要的字段。用相同修改任务确认输出与依赖覆盖，再比较未缓存输入和成功完成耗时。
3. **缩短可见等待。** 根据候选、导出与浏览器加载的实测时间定位瓶颈；结合已有增量执行，先展示可续改候选，重任务按依赖与对象版本处理。同步必须保留明确来源，过期结果不得覆盖新候选。
4. **再替换策略。** 用相同已记录任务比较基线与新算法的成功率、实际费用和完成耗时。OCBA 先用于确实需要重复评估的固定候选；只有多步搜索带来可测收益时接 MCTS。

当前已实现采集、计价、分段耗时与算法接口；输入精简、同步改造及具体优化算法仍是后续工作。

## 开发检查

```powershell
$env:PYTHONPATH = "$PWD;$PWD/apps/archflow-studio/api"
python -m pytest tests/monkeymonitor apps/archflow-studio/api/tests/test_monitoring.py -q
python tools/archcheck.py
```

CREATE 的理由：`ports.model` 已拥有模型调用回执，但不拥有跨应用用量、费率或算法预算建议；
建模与出图 owner 也不应承担开发会话计量。用户明确要求独立 MonkeyMonitor，故建立此同级工程包。
项目持久化继续由 P036 独占，ArchFlow 核心与两条领域工作流均不导入 Monitor；共同宿主完成装配。
