# P115 — 能力总索引与逐项整理（冻结的历史索引）

**状态：blocked，已冻结（2026-09-24，GH-60）。** 按 [#60](https://github.com/cogco1/MonkeyHub/issues/60) 的决定，
本卡只作历史索引：不是任务清单，不占任何写入路径，不从这里推导当前任务，也不再回填进度。
新工作从 GitHub Issue 开始；谁正在改哪些路径，以 `python tools/devctl.py work` 列出的 active／review lane 为准。
现有能力与归口查 `python tools/devctl.py capability <目标>`、`module <关键词>` 和 [SYSTEM_MAP](../../SYSTEM_MAP.md)。

原 lane 已全部关闭，历史 write_scope 已释放；已交付的实现不重新开发。C01–C05 与 C07 已交付，
C11 中 P108／P115 的部分由 GH-60 完成。冻结前的完整内容（功能节点图、R／M／A／O／W 能力清单、
C01–C12、F01–F14 与逐项交付记录）保留在 Git：
`git show 04e71961:docs/mapping/planning/P115-capability-consolidation.md`。

## 仍有效的剩余验收由谁承接

| 原 P115 内容 | 承接位置 |
| --- | --- |
| 整轮上下文、耗时与工具往返（含并入的 #8） | [#32](https://github.com/cogco1/MonkeyHub/issues/32) |
| SketchUp 文件往返；Phase 0 仍开放 | [#53](https://github.com/cogco1/MonkeyHub/issues/53) |
| 专用 3D 生成器比较；通用推理 Agent 调用建模工具的比较 | [#50](https://github.com/cogco1/MonkeyHub/issues/50)、[#51](https://github.com/cogco1/MonkeyHub/issues/51) |
| 桌面包的真实旧项目迁移演练（#21 余项） | [#54](https://github.com/cogco1/MonkeyHub/issues/54) |
| 签名发行与更新通道（#21 余项） | [#58](https://github.com/cogco1/MonkeyHub/issues/58) |
| MonkeyBoard 新用户盲测（#23 余项） | [#86](https://github.com/cogco1/MonkeyHub/issues/86) |
| 直接建模后续（#14）：变换控件；标准视图、推拉，以及全场景吸附（含遮挡点过滤和查询实测） | [#136](https://github.com/cogco1/MonkeyHub/issues/136)、[#137](https://github.com/cogco1/MonkeyHub/issues/137) |
| C08／F04 方法＋工具＋检查的 Agent 闭环；2026-09-11 记录的设计循环缺口；能力目录与 MCP 的覆盖 | [#185](https://github.com/cogco1/MonkeyHub/issues/185) |
| F02 案例与规范检索 | [#122](https://github.com/cogco1/MonkeyHub/issues/122) |
| F12 设计搜索策略 | [#121](https://github.com/cogco1/MonkeyHub/issues/121) |
| O04／F06／F07 平立剖投影、图面表达与排版 | [#244](https://github.com/cogco1/MonkeyHub/issues/244)、[#66](https://github.com/cogco1/MonkeyHub/issues/66) |
| F09 渲染；F10 表达与成果状态的接入 | [#253](https://github.com/cogco1/MonkeyHub/issues/253)、[#216](https://github.com/cogco1/MonkeyHub/issues/216)、[#218](https://github.com/cogco1/MonkeyHub/issues/218)；[#223](https://github.com/cogco1/MonkeyHub/issues/223) |
| 会议转写接入 | [#254](https://github.com/cogco1/MonkeyHub/issues/254) 的 Meeting → Work → Report |
| 草图入口余项（#46）：自动视觉描图与实稿评测 | [#120](https://github.com/cogco1/MonkeyHub/issues/120) |
| A04 项目要求进入 Studio 校核 | [P110](P110-canonical-state-projection.md)（登记中的 legacy 卡） |
| Studio 真实使用：独立成员试用、真实改稿与楼梯通道试用的待决问题 | [P108](P108-vibe-modeling-frontend.md) |

已关闭 Issue 的余项按 owner 关闭时的说明处理，不在这里重开：#12 的运行时恢复和 #14 的真实大模型长时试用，
只在发现具体问题时另开窄 Issue；#13 关闭时未要求真人交接；#24 暂不计划。

## 冻结时仍未确定是否需要

以下不是待办；是否继续由 owner 决定，决定要做时从 GitHub Issue 开始。

- C06 合并普通意图的重复分流：Hub Agent 改走确定性接口后是否仍需要，未核对。
- C09 旧 developed／portfolio 投影的折叠、C10 确定性 proposal 的 provider 往返：是否仍值得做，未核对。
- C12／F13 开发者交接：#13 已由第二个账号从 fresh clone 复跑，是否还需要一次真人交接，未定。
- 线网成面、面分割、圆弧参数编辑、prism 局部开口的草图入口、组件／实例编辑与测量：#136／#137 不含，是否仍要做未定。
- F01 任务书自动拆解、F05 环境分析、F14 MonkeyFab 余项（Stage 回写、实机发送、排料、激光／CNC）：没有 Issue 承接，是否仍要做未定。
- 本地优先共享项目的跨机器网络与真人协作：未验证，是否仍需要未定。
- 从任意 PDF／图片一键提取图面风格：未实现，是否仍需要未定。
