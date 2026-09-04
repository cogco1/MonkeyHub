# ArchFlow 愿景

> 当机器越来越擅长生成答案，我们想让它重新学会陪人推敲。

ArchFlow 试图把设计从“结果的序列”重新表示为“持续的推敲过程”。它研究如何把原本存在于人脑、草图、对话和反复修改中的推敲，变成机器可以理解、保存和参与的计算对象。

本文定义长期方向；当前实现见 [ARCHITECTURE.md](ARCHITECTURE.md)，开发周期见 [P111](mapping/planning/P111-continuing-design-cycle.md)。

## 研究愿景：计算未完成的设计

一个还没有想完的建筑，在计算机里应该是什么？

真实设计同时包含已经确定的部分、暂时必须保留的条件、模糊意图、尚未解决的问题、正在比较的方案、被否定但仍有价值的尝试，以及人的判断和外部证据。ArchFlow 希望这些内容能与几何一起持续演化。

研究框架可以简写为：

```text
Design State = Artifacts + Questions + Commitments + Proposals
             + Judgments + Evidence + Uncertainty
```

这是概念表达，不是当前 `StateRecord@1` 的字段定义，也不预设七个模块或七种新记录。内容可以通过既有记录及其关系共同表达；进入实现时按具体使用场景确定最小表示。问题可以保持开放；判断应保留作出者、所针对的方案和依据；被拒绝的尝试仍可以成为后续推敲的材料。

研究对象是建筑怎样逐渐成为它自己。CAD、BIM 与生成式工具都可以参与这一过程；这里关注的是跨越工具、会话和版本后，推敲能否继续。

## 技术愿景：项目的理解能够独立于模型存在

```text
Human + Agent → Proposal → Evolving Design State → Compiler → Geometry
                    ↑               │                           │
                    └──── Questions, inspection and judgment ───┘
```

设计状态保存项目的内容、依据和变化关系。模型提供解释、搜索和提案能力；执行后端实现适合自身的几何操作。长期目标是更换模型或执行环境后仍能继续同一个项目。

移除 LLM 后，项目仍应能读取当前设计、已接受决定、未解决问题、历史方案及其判断依据。新的智能提案可能暂停，已有理解仍可供人检查和修改。更强的模型有望改善提案与搜索；编译和几何性能则需要各自的工程改进，不能由模型升级自动推定。

“设计状态拥有自己”表示项目理解不依附某个代理的上下文。人的设计决定、授权和责任仍有明确主体；状态不会自行获得接受设计的权力。接受、继续修改候选、发布阶段成果是不同动作，按现有项目契约处理。

## 产品愿景：让设计更容易继续

MonkeyArch 是用于在建筑实践中检验这一研究的专业建模环境参考实现。长期目标是开放、可扩展；具体开放范围和许可由实际发布决定。

产品的核心动作是 **Continue**，核心循环是：

```text
Notice → Question → Propose → Externalize → Judge → Revise → Commit
```

这是一组可以回返的设计活动，不是要求每次对话完成七步的向导。Externalize 指把想法变成可供共同查看和比较的模型、图纸或其他表达；Commit 指人的明确接受，具体作用范围应清楚可见。

当建筑师说“这个入口感觉有点重”，系统应能保存这个问题，提供可供辨别的解释，把备选方案呈现出来，让人比较并说明判断。下一轮继续使用这些判断，保留既有承诺；需要改变承诺时明确提出修订，最后由人决定接受什么。重新打开项目后，这段工作应能接续。

## 更长远的问题

当机器越来越能够生成复杂设计结果，人如何继续保持判断、修改、追问和重新定义问题的能力？

建筑提供了一个目标多元、约束交织、周期漫长且后果昂贵的研究场景。ArchFlow 以建筑实践检验 **Human Judgment + Machine Intelligence + Persistent Design State** 如何共同支持持续演化的设计。向其他设计领域迁移是可能的研究方向，不能替代建筑场景中的验证。

## 对外表述

**中文**

ArchFlow 试图把设计从“结果的序列”重新表示为“持续的推敲过程”。它探索将问题、承诺、备选方案、证据、判断和不确定性与几何共同表示为持续演化的设计状态，使人与机器能够围绕同一个设计不断提出、查看、比较、修改和重新思考，并让尚未解决的问题保持开放。

MonkeyArch 是用于在建筑实践中检验这一方法的专业建模环境参考实现。

**English**

ArchFlow explores a computational representation of design as an ongoing process of deliberation. It seeks to preserve questions, commitments, alternatives, evidence, judgments, and uncertainty alongside geometry in an evolving design state, allowing humans and machines to continuously propose, inspect, revise, and reconsider a design while keeping unresolved questions open.

MonkeyArch is a reference implementation of a professional modeling environment for testing this idea in architectural practice.

## 如何判断方向成立

用同一项目的连续设计片段检验：隔开会话或更换模型后能否接续；人的判断能否影响下一轮；被拒方案及其理由是否仍可使用；已有承诺是否得到保留或明确修订；人能否重新定义问题。记录实际耗时、重复解释和返工情况，再判断系统是否改善了设计过程。生成数量与模型复杂度只能说明局部能力。
