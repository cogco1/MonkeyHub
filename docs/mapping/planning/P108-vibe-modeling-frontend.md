# P108 — Studio 候选操作与真实使用闭环

**状态：blocked（2026-09-24 按 [#60](https://github.com/cogco1/MonkeyHub/issues/60) 收尾，GH-60）。** 本卡不占任何写入路径，
只等下文的一项 owner 决定。Studio 的编辑与审阅、候选、来源绑定、A／B 继续、批注、图页视觉输入和界面整理已经交付，
Studio 界面已并入 MonkeyHub（#126、#127）；交付记录保留在 Git：
`git show 04e71961:docs/mapping/planning/P108-vibe-modeling-frontend.md`。
已交付部分不重新开发，也不再等待原 Studio 单页恢复。新工作从 GitHub Issue 开始；当前协作看 `python tools/devctl.py work`。

## 剩余验收

- **独立成员试用：** 一位未参与开发的人独立完成一次真实的修改与审阅，由 [#86](https://github.com/cogco1/MonkeyHub/issues/86) 的首次使用盲测承接；自动化测试不能代替。
- **真实改稿的建筑效果：** 以真实项目中的结果判断，不以候选生成、接口成功或测试通过代替；设计循环的产品验收由 [#185](https://github.com/cogco1/MonkeyHub/issues/185) 承接。
- **图页视觉输入：** 页面图像、批注叠图与参照页送入真实模型后的读图和改稿效果还没有真实任务记录；在一次真实修改上核对之前，不宣称它对图纸有效。

## 待决：本卡保持 blocked 的原因

[ARCHITECTURE 的 Development order](../../ARCHITECTURE.md#development-order) 第 1 步，即既有项目的楼梯与侧向通道改稿，
是否仍是首个真实项目试用，owner 尚未决定，也没有 Issue 承接。
