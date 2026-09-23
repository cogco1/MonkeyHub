# 把评价预算用在会改变选择的地方

OCBA 研究讨论稿 · 2026-09-21 · 建议讲述 5–10 分钟

本次讨论希望确定：在固定的一组设计候选中，自适应分配评价预算能否提高选择可靠度；下一轮怎样用有明确不确定来源的结构评价检验这一收益。MonkeyHub 提供候选、评价和结果复查的实验环境，研究贡献由实际比较决定。

**本实验分配的对象是各候选的追加评价次数。** 总预算预先给定，OCBA 根据已观测的差距、方差与费用调整后续分配；它不生成方案、不决定建筑审美，也不写 Canonical State。

## 1｜先区分两类性能问题

等待一次修改完成，要测“到可用候选的时间”，涉及模型往返、工具执行、读回和界面呈现，另见[性能实验方案](performance-experiments.md)。本轮则测“已有多个候选时，同等评价预算能选得多可靠”。输入 token 减少、评价次数减少与墙钟提速分别报告；OCBA 不会直接让聊天模型或有限元求解器运行得更快。

## 2｜OCBA 与代理模型分别做什么

**生成器产生候选，评价器测量候选，OCBA（Optimal Computing Budget Allocation）决定下一次评价给谁。** 当候选均值接近、观测波动较大时，追加评价可能改变最终选择。经典问题的目标是在固定预算下提高选中真实最优候选的概率，即 PCS。

**代理模型（surrogate model）近似一次昂贵评价。** 它通过已计算样本预测新输入的响应，减少单次求解的代价，同时带来预测误差、训练成本及适用范围。

```text
生成器 → 固定候选 → 硬有效性检查 → 评价器 → 均值 / 方差 / 成本
                                  ↑                ↓
                              下一次评价 ← 预算分配器
                                                   ↓
                                         预算结束后的候选比较
```

代理模型可以成为评价器的一部分，但它的系统偏差不能直接作为独立采样噪声交给 OCBA。加入代理模型后，需要把训练、更新、校准和高保真复核一并计账。当前尚未训练代理模型，也尚未完成 OCBA 与 FEA 的真实耦合。

## 3｜现有实验已经覆盖什么

当前 `MassingEvaluator` 是 **massing metrics + validity adapter**，不是完整的建筑设计评价器。其边界如下，详见[原始报告的验收映射](../../labs/candidate_evaluation/BENCHMARK.md#evaluation-correctness-and-acceptance-mapping)：

| 层 | 当前已有 | 当前没有 |
| --- | --- | --- |
| hard validity | exact binding，以及声明的 site bounds、height、FAR 检查 | 完整规范、Stage／保护关系及真实建筑验收 |
| objective measurement | footprint、GFA、floor count、height | usable area、daylight、energy、structure、circulation／空间质量 |
| uncertainty | 合成采样的 count、mean、variance、SE | 已校准物理不确定性、代理模型误差、偏好不确定性 |
| preference | 无建筑偏好模型 | architectural taste／design-quality model |

| 项目 | 真实／保留部分 | 人工／未测试部分 |
| --- | --- | --- |
| candidate state／P036 reopen | 24 个候选经真实接口生成、保存、重开 | 不因此成为完整建筑验收 |
| massing geometry／GFA | 保留体量及实测面积 | 非任意 CAD 或 usable area 验证 |
| 四项确定性指标 | 已实现，但范围有限 | 不代表综合建筑评价 |
| Gaussian observation noise | — | 人工噪声 |
| per-sample cost units | — | 人工费用，非实测求解时间 |
| utility mean = GFA / 100 | GFA 来自实测 | 缩放与最大化方向是实验 policy |
| architectural quality | — | 未测试 |
| physical simulation | — | 未进入本次 OCBA 核心实验 |

现有实验比较均分、轮询、按方差分配、ε-greedy、经典 OCBA 和固定成本 OCBA。预热与失败尝试都占预算；无效候选不进入软指标比较。

经典分配使用均值差与方差的比例关系；成本扩展假设单次费用已知、固定、可加。两者依据渐近近似，不提供当前有限预算下的成功保证。初始采样不足尤其可能影响后续分配。[Chen 等，2000](https://doi.org/10.1023/A:1008349927281)；[Wu 与 Zhou，2018](https://arxiv.org/abs/1811.12183)。

本阶段已证明 evaluator → statistical summary → allocator 接口可运行和重放，简单策略与 OCBA 可在同一预算下比较。它是算法实验台／sanity check；真实建筑评价器尚未进入核心比较，不以测试数量替代研究结论。

## 4｜历史合成实验：三组关键结果

旧轮次为 171 个条件、每条件 200 次外层重复。下表只保留三个最有解释力的条件，来源为[原始报告](../../labs/candidate_evaluation/BENCHMARK.md#highest-budget-comparisons)与[历史聚合 CSV](../../labs/candidate_evaluation/benchmark_results.csv)，不与下一节的新轮次合并。

| 条件／预算 | 关键 PCS 对照 | 能回答什么 |
| --- | --- | --- |
| Close top-2／900 samples | Equal 0.750；OCBA 0.935 | 该合成条件下，追加分配提高了选择可靠度 |
| Heterogeneous variance／900 samples | ε-greedy 0.915；OCBA 0.760 | OCBA 不是普遍赢家，噪声结构影响结果 |
| Heterogeneous cost／4,500 cost units | Equal 0.760；Classic OCBA 0.780；Cost OCBA 0.770 | 成本感知规则没有显示普遍优势 |

另一个历史对照中，同为 900 次采样，异成本场景的均分花费 3,780 成本单位、经典 OCBA 约 11,579，PCS 分别为 0.740、0.870。支出不同，不能单独归因为分配更高效；因此后续复核固定成本，直接估计策略的配对差值。

## 5｜新轮次：同成本配对复核

新轮次固定每次总成本为 **4,500**；两种场景、三种策略，每条件 **400 次外层重复**，共 2,400 次执行。策略共享同一试验／候选随机流，新种子在运行前固定。这是探索性 pilot，并未完成充分的功效设计。数字及区间来自[配对分析](../../labs/candidate_evaluation/PAIRED_ANALYSIS.md#实验与结果)与[配对 CSV](../../labs/candidate_evaluation/paired_results.csv)。

| 场景 | 均分 PCS | 经典 OCBA | 成本 OCBA |
| --- | ---: | ---: | ---: |
| 异成本合成候选 | 70.50% | 72.75% | 73.50% |
| 固定体量候选 | 93.25% | 96.00% | 96.50% |

| 相对均分的差值 | 点估计 | 四项比较同时覆盖的 95% 区间 |
| --- | ---: | ---: |
| 异成本／经典 OCBA | +2.25 个百分点 | −4.94 至 +9.37 个百分点 |
| 异成本／成本 OCBA | +3.00 个百分点 | −4.68 至 +10.59 个百分点 |
| 固定体量／经典 OCBA | +2.75 个百分点 | −3.11 至 +8.51 个百分点 |
| 固定体量／成本 OCBA | +3.25 个百分点 | −2.89 至 +9.28 个百分点 |

**本轮观察到正向点估计，但四个区间均包含零，尚不能确认优于均分。** 这也不是等效性证明。两类场景中，OCBA 减少了采样次数，但平均消耗成本仍均为 4,500；不能把次数下降写成节省了费用。

配对区间直接描述同一试验中两种策略的选择差异，不能用各策略的边际区间替代；见[配对差值图](../../labs/candidate_evaluation/paired_pcs.svg)。固定体量候选是真实保留记录，噪声与成本仍是人工条件，不能解释成建筑质量提升。单一预算点也不能回答“达到相同可靠度能节省多少费用”。

## 6｜下一步怎样接入 FEA

现有结构 lab 已有 PyNite 3.2.0 线性静力求解、来源绑定及梁／柱解析校核。同一确定输入反复求解不会产生新的独立观测；采样噪声必须来自明确的随机输入或随机评价过程。[现有结构实验](../../labs/structural_analysis/README.md)

后续多保真研究路线如下，尚未实现；首轮先固定真实 FEA 问题，再判断是否加入代理模型：

```text
固定建筑／结构候选 → 确定性指标与硬检查
                              ↓
            可选评价器：便宜代理模型／中高保真 FEA
                              ↓
              值 + 采样不确定性／模型偏差 + 成本
                              ↓
        预算分配：下一候选 × evaluator × fidelity × budget
                              └────────→ 下一次评价
```

建议从一个可核验的结构任务开始：

| 项目 | 首轮建议 |
| --- | --- |
| 候选 | 复用双跨框架，固定 5–8 个同材料用量、左右梁刚度分配不同的候选；记录截面、材料、支座和几何来源，比较中不生成新候选 |
| 随机输入 | 首轮只随机一侧荷载幅值，另一侧荷载及材料固定；分布由合作方提供依据，或明确标为实验假设。双侧相关荷载另列后续条件 |
| 比较目标 | 建议先选“最大竖向位移的期望”这一项，保留原始位移与材料量；超限概率是另一种目标，须另行声明阈值与参考，避免所有梁统一加粗导致显然占优 |
| 参考答案 | 有解析期望时使用独立解析参考；否则用独立高预算／积分参考并报告误差。参考不能读取或复用政策试验样本 |
| 对照 | 均分、经典 OCBA、成本 OCBA、ε-greedy；racing 作为后续新增基线，不写成已实现 |
| 报告 | PCS 或容许误差内的正确选择率、regret、实测求解次数、总 CPU／墙钟时间、失败、分配开销 |

若参考均值区间仍有重叠，就不能把某一候选硬称为“真实最优”。应先缩小参考误差，或预先声明可接受差距，改测 good selection。若采用有限且很小的离散荷载集合，应同时保留“直接穷举并缓存”的基线；这种情况下 OCBA 未必值得使用。

首轮的目标是打通可解释的评价接口与选择问题。现有小型线性算例求解很便宜，可能由调度开销主导；是否存在净时间收益，需要实测，不能通过人为延时制造。

## 7｜速度与代理模型各自的下一道实验

**实测时间。** 第一轮固定计算资源，记录启动、准备、分配、求解、复核各段及完整墙钟。人工费用用于受控算法实验，实测运行时间用于判断部署收益；二者不互换。若求解时长随候选变化，先用独立预跑估计成本并冻结策略输入，另行统计实际总支出。

**并行。** 当前 `--workers` 并行的是独立 Monte Carlo 试验，单次分配仍为顺序执行。真正的并行分配还要处理未返回的样本、已占预算、重复派发和最后一批的等待。相关研究把这些问题单独建模，不能直接由顺序结果推断。[Avci 等，2023](https://doi.org/10.1145/3618299)

**代理模型。** 等真实 FEA 评价问题稳定后，再做二因素比较：高保真／代理辅助 × 均分／自适应分配。高保真标注、训练、更新与最终复核全部计入；单次预测加速和总任务加速分别呈现。

## 数据与方法附注

- 第 4 节为历史汇总，第 5 节为新轮次，不合并重复数。旧目录的 raw gzip 已截断；新轮次使用独立生成的完整数据，不能据此宣称重新验证了旧 raw。
- 本轮详细统计、区间方法、来源与复现入口见[配对分析](../../labs/candidate_evaluation/PAIRED_ANALYSIS.md)。原始大文件保存在明确的本地外部实验目录，不放入设计项目或公开报告正文。
- 文案初稿由 Claude 协助整理，最终数字、机制解释与研究边界经本任务核对。未使用生成文本充当实验数据。

## 8｜需要合作者判断的六个问题

1. 当前 classical OCBA baseline、预热和固定成本扩展的实现方式是否合理？
2. 合作者已有算法处理 single-fidelity repeated sampling，还是 candidate × evaluator × fidelity × cost？
3. surrogate prediction error／bias 应怎样进入 allocation，何时必须以高保真复核？
4. sampling variance、surrogate/model error、fidelity bias 与 input uncertainty 如何分别表达，避免混作一种噪声？
5. 若首个真实案例采用 structural surrogate + reference FEA，应怎样固定候选、目标、参考误差和含训练／复核成本的 benchmark protocol？
6. 论文应聚焦“OCBA applied to architecture”，还是更一般的“adaptive allocation of expensive multi-fidelity design evaluation”？现有证据能支撑哪一层贡献？
