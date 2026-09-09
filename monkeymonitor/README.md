# MonkeyMonitor

独立的工程用量与预算工具，与 `monkeyarch/`、`monkeydiagram/` 同级。
可以单独启动，无额外 Python／Node 依赖。Studio 只负责把真实调用结果交给它。

## 已可使用

- 读取显式指定的 Codex 会话，统计实际报告的输入、缓存、输出及推理用量。重复累计快照不重复计入；计数重置或历史断点会显示提示。
- Studio 的 Codex／Anthropic 意图调用返回 token 计数；成功、澄清、输出无效及 provider 失败均可记录。没有用量的调用保留为未计量。
- 独立页面沿用 Monkey 系列深浅色、字体、顶栏与控件；按来源、模型与阶段查看用量、缓存比例和调用耗时。明细可排序，单次用量可直接带入美元计价器。
- 通用 `Algorithm.choose(context)` 接口及 `FirstAvailablePolicy` 基线已实现。接口返回建议，宿主仍负责执行、预算扣减与结果验收。

## 启动

从源码仓根目录运行，Python 3.12 以上：

```powershell
python -m monkeymonitor serve
```

打开 `http://127.0.0.1:8788`。未指定数据源时显示空列表，计价器仍可使用。

读取一个明确的 Codex JSONL 会话；子代理需要分别传入自己的文件：

```powershell
python -m monkeymonitor serve --codex-session 'C:/path/to/rollout.jsonl'
python -m monkeymonitor report --codex-session 'C:/path/to/rollout.jsonl'
```

`report` 向标准输出返回统计元数据，不写文件。不扫描其他任务，不导出提示词、回答、工具内容或凭据。
选中的文件每次刷新重新读取，因此可跟随正在增长的会话。它不能凭 token_count 把开发调用细分为“查代码／推理／工具”，也不能从累计事件可靠反推模型调用耗时。

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

1. **先找大头。** 用当前采集器覆盖真实 Studio 请求和开发会话；分别检查输入、缓存命中、输出、失败率及耗时。当前只测到模型意图调用；排队、几何编译、导出、发布与前端同步尚未分段计时。
2. **缩小模型输入。** 在现有 intent 编译器中复用稳定规则前缀，把状态上下文收敛到选择对象、受影响依赖及完成本次修改需要的字段。用相同修改任务确认输出与依赖覆盖，再比较未缓存输入和成功完成耗时。
3. **缩短可见等待。** 在现有 jobs／events 链补齐排队、执行、导出和发布耗时；结合已有增量执行，先展示可续改候选，重任务按依赖与对象版本处理。同步必须保留明确来源，过期结果不得覆盖新候选。
4. **再替换策略。** 用相同已记录任务比较基线与新算法的成功率、实际费用和完成耗时。OCBA 先用于确实需要重复评估的固定候选；只有多步搜索带来可测收益时接 MCTS。

本轮完成采集、计价与接口；上述输入精简、分段耗时、同步改造及具体优化算法仍是后续工作。

## 开发检查

```powershell
$env:PYTHONPATH = "$PWD;$PWD/apps/archflow-studio/api"
python -m pytest tests/monkeymonitor apps/archflow-studio/api/tests/test_monitoring.py -q
python tools/archcheck.py
```

CREATE 的理由：`ports.model` 已拥有模型调用回执，但不拥有跨应用用量、费率或算法预算建议；
建模与出图 owner 也不应承担开发会话计量。用户明确要求独立 MonkeyMonitor，故建立此同级工程包。
项目持久化继续由 P036 独占，ArchFlow 核心与两条领域工作流均不导入 Monitor；共同宿主完成装配。
