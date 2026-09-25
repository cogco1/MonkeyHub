# MonkeyMonitor

MonkeyMonitor 是 MonkeyHub 的工程观测与费用估算模块。它继续拥有 **用量记录、任务 trace、费率估算与诊断算法**；用户界面现在由 MonkeyHub 的 **Usage / 用量** 页面承载，不再维护第二套独立 Web App。

这条边界是刻意的：

```text
Hub / Studio / CAD / Codex observations
                 ↓
      MonkeyMonitor UsageLog + trace views
                 ↓
        MonkeyHub · Usage page
```

`monkeymonitor` 不写建筑项目状态，不接受设计，不执行候选，也不成为 P036 / Canonical State 的第二个权威。它的诊断日志位于调用方明确提供的非项目目录；页面只是读取同一份记录。

## 在 MonkeyHub 中使用

打开 MonkeyHub，右侧 **Usage / 用量** 进入监控页面。Hub 启动时会监督一个内部 MonkeyMonitor API worker；页面关闭或重新打开不会改变日志，也不会改变项目状态。

Usage 页面目前提供：

- 模型调用的缓存输入、未缓存输入、输出与请求往返 P50；缺失观测保持未知，不补成 0。
- `TurnTrace@1` 任务时间线：Hub / Agent / Studio / CAD / Client 阶段、阻塞标记、诊断与原始安全元数据。
- 原始用量记录与项目过滤。
- Hub 自动绑定的 Codex 会话，以及按明确 JSONL 路径补充的诊断来源。
- 显式选择费率的费用估算器；不会把 API 等价值冒充订阅实际扣款。

MonkeyHub 页面通过只允许 owning Hub loopback origin 的 CORS 访问内部 Monitor API。Monitor 不再提供 `index.html`、`app.js` 或自己的样式/导航壳；受 Hub 管理时访问其根地址会回到 MonkeyHub 的 Usage 页面。独立启动 API 时根地址只返回服务说明。

## CLI 与内部 API

`report` 是保留的无 UI 诊断入口：

```powershell
python -m monkeymonitor report --data-dir C:\explicit\diagnostics
python -m monkeymonitor report --codex-session C:\path\to\rollout.jsonl
```

它向标准输出返回 JSON，不写项目文件，不导出提示词、回答、工具正文或凭据。

`serve` 也保留，供 MonkeyHub 的受管 worker、测试和明确的诊断集成使用；它现在是 **API-only** 服务，而不是第二个产品入口：

```powershell
python -m monkeymonitor serve --data-dir C:\explicit\diagnostics
```

主要接口：

| 接口 | 作用 |
| --- | --- |
| `GET /api/health` | 受管进程身份与健康状态 |
| `GET /api/events` | 当前归一化用量记录 |
| `GET /api/traces` | `TurnTrace@1` 任务视图 |
| `GET /api/traces/export?trace_id=...` | 导出一条经过过滤的 trace |
| `GET/PUT /api/sources/codex` | 查看/设置明确的 Codex JSONL 补充来源 |
| `GET /api/rates` | 读取内置参考费率目录 |
| `POST /api/quote` | 对调用方提供的 token + rate 做确定性估算 |

服务只监听 loopback。受 Hub 管理时，跨 origin 浏览器访问只允许启动它的那个本机 Hub（`127.0.0.1` / `localhost` 同端口）；其他 origin 和非 loopback Host 被拒绝。

## 数据与用量口径

Studio / Hub 的观测写入显式的 `MONKEYMONITOR_DATA_DIR`，默认由 MonkeyHub 指向它自己的 runtime diagnostics 目录。日志属于工程诊断，不属于建筑项目文档。

`UsageLog` 使用分段 JSONL：默认每段 8 MiB、保留 3 个历史段。写入和读取使用跨进程锁。写入的线程锁与文件锁共用 20 ms 的竞争等待预算，使请求返回与候选工作线程同时结束时能够保留两者的观测；超时仍跳过本次观测，后续成功记录携带“曾缺失观测”提示。读取不等待，占用则返回 503，而不是假装这是一个空的新日志。该预算只限制主动等锁，不能承诺操作系统调度与磁盘 I/O 的总耗时；没有延迟写入队列，也不会重试业务操作。

Token 口径：

- `input_tokens` 是总输入，包含缓存读取与缓存写入。
- `cached_input_tokens` 是总输入中的缓存读取子集。
- `cache_write_input_tokens` 是总输入中的缓存写入子集；1 小时写入是它的子集。
- `output_tokens` 已包含 `reasoning_output_tokens`，不能再加一次。
- 没有 provider 数值的字段保持 `null`。

同一事件的累计快照按 `event_id` 去重。Hub/Codex 能证明同一原生会话时，trace 汇总不会把 Hub 活动区间和 Codex 原生 token 再算两份。

## TurnTrace

`GET /api/traces` 是对 UsageLog 的只读投影，不建立第二个 trace store。它把能明确关联的阶段组织到一个任务根下，并保留五条显示泳道：Agent、Hub、Studio、CAD、Client。

关键限制：

- 父子阶段与并行阶段可以重叠，不能相加还原总时长。
- `elapsed_ms` 只有存在完整根区间时才成立。
- `model_rounds` 只有能把真实用量事件绑定到明确模型请求边界时才给数值。
- `first_visible_ms`、客户端加载、CAD 等时间使用各自产生者记录的边界，不拿 token 时间差补算。
- `first_candidate_ms` 是根请求开始到本轮首次成功生成候选的对象读回结束的时间。同一 trace 内必须先有同一 `run_id` 的成功 Studio `candidate` 生成区间，生成起止完整且不早于本轮起点；随后才采纳对象 inspection 完备、结束时间已记录的成功 `candidate_readback`。只读取旧输入候选、缺生成证据或缺结束时间时保持未知；比较失败不会抹去成功读回，job 成功、HTTP 成功或尚在运行的候选不算。该指标不代替 `verified_ms` 的显式验证，也不代表已满足空间意图或完成界面绘制。
- critical path 只标注有明确阻塞证据的区间；并行分支缺少等待先后证据时保留未归因。
- 根请求已经结束、子阶段只有开始记录时，投影状态为 `incomplete`，时长为 `null`；不把当前读表时间当作其结束时间。原日志保留，后来收到真实结束记录后恢复其实际区间。缺少结束记录的工具也使工具覆盖状态为 `incomplete`。
- trace export 过滤本机路径、正文与任意自由文本，只保留允许的诊断代码、计数和摘要身份。

输入重复诊断只表示“当前记录里的输入身份相同”。它不会自行断言某次请求本来可以删除；是否能复用还要满足 exact source、缓存可用性、provider 规则与结果绑定。

计时修复采用 [Python 3.12 `Lock.acquire(timeout)`](https://docs.python.org/3.12/library/threading.html#threading.Lock.acquire) 与现有非阻塞文件锁；参考 [Portalocker 3.2.0 的有界锁重试](https://github.com/wolph/portalocker/blob/v3.2.0/portalocker/utils.py)（[BSD-3-Clause](https://github.com/wolph/portalocker/blob/v3.2.0/LICENSE)），使用同一单调时钟截止时间，未引入该依赖。保留 [OpenTelemetry Python 1.37.0 `SimpleSpanProcessor`](https://github.com/open-telemetry/opentelemetry-python/blob/v1.37.0/opentelemetry-sdk/src/opentelemetry/sdk/trace/export/__init__.py)（[Apache-2.0](https://github.com/open-telemetry/opentelemetry-python/blob/v1.37.0/LICENSE)）的结束事件同步交付、诊断失败隔离思路；未采用其批处理后台线程，因为本次缺口是短暂锁竞争，增加队列会额外引入关闭时排空和事件顺序问题。父阶段结束不证明子阶段结束，符合 [OpenTelemetry Trace API 的独立 span 生命周期](https://opentelemetry.io/docs/specs/otel/trace/api/#end)。

## Codex 会话来源

Hub 管理的 Monitor 启动参数包含：

```text
--codex-bindings-url http://127.0.0.1:<hub-port>/api/chat/usage-sources
```

这个私有 Hub 投影只提供项目/会话身份。Monitor 在 `CODEX_HOME`（默认 `~/.codex`）中只读定位对应日志并核对身份，不读取 Hub 聊天正文，也不按“最近任务”或工作目录猜归属。

需要额外诊断时，可在 Usage 页面逐行填写明确的 JSONL 绝对路径，或在 CLI 使用多个 `--codex-session`。重复路径和重复快照不会重复计数；无法核实的旧式继承关系保持未知。

## 费率

`rates.json` 是带来源与生效日期的参考目录，不是某个账户的账单。自动历史价格只有在 provider、model、billing plan 与日期可以精确匹配时才成立；匹配不到就保持未知。

写入方按自己的连接记录 `details.billing_plan`，取值只来自封闭词表 `usage.BILLING_PLANS`，推导只有 `pricing.billing_plan()` 一处：

| 事件的 provider（连接） | 写入方 | billing_plan |
| --- | --- | --- |
| `anthropic` | Studio 意图编译，Anthropic Messages API | `api-standard` |
| `codex` | Studio 意图编译，`codex exec`（忽略用户配置） | `api-standard` |
| `claude` | Hub 对话，Claude CLI | `api-standard`；消息 usage 报告 fast、priority、batch 或 US-only 推理时不写 |
| `coding-plan` | Hub 对话，第三方兼容端点 | `coding-plan`（包月，没有公开的逐 token 价） |
| `openai` | Codex 会话日志记录的 model provider | `api-standard` |

`api-standard` 指该 provider 公开价目表的标准同步档。Gemini 出图不写 plan：输出混合文本与图像 token，单一输出费率无法计价。目录为 Anthropic API 与 Claude CLI 两个连接各列 Claude 标准价（2026-09-25 读取官方价目表）。OpenAI 行分短、长上下文两档，单个事件无法判定属于哪档，因此匹配结果是 `ambiguous`，不猜档位；`codex` 连接没有自己的行，也不借用 `openai` 的行。

页面中的手动计算器要求用户明确选择费率。`quote()` 只对互不重叠的 token bucket 计价：普通输入、缓存读取、短缓存写入、1h 写入、输出。任一所需计数或费率缺失时，`amount_usd` 保持 `null`，同时返回已知小计与缺失项。

## 算法建议接口

`Algorithm.choose(context)` 与 `FirstAvailablePolicy` 仍是 Monitor 的通用工程预算建议接口。它只返回建议；宿主负责执行、预算扣减与结果验收。MonkeyMonitor 不因此获得候选执行、设计接受或项目发布权限。

## 基准与测试

真实任务基准配置仍位于 `tests/monkeymonitor/benchmarks.json`，可运行：

```powershell
python tests/monkeymonitor/run_turn_benchmark.py --scenario simple-create --output C:\explicit\benchmark-output
```

它创建可丢弃的测试 Hub/Studio/OCCT 项目并把 trace 留在明确输出目录。provider 响应时间是测量值，不设固定秒数 CI 门槛。

核心检查：

```powershell
python -m unittest tests.monkeymonitor.test_core tests.monkeymonitor.test_trace tests.monkeymonitor.test_turntrace_contract tests.monkeymonitor.test_server
cd apps/monkeyhub/web
npm test
npm run build
```

Web 表达测试随页面 owner 移到 `apps/monkeyhub/web/`；`monkeymonitor/web/` 已退役，不再维护平行浏览器实现。
