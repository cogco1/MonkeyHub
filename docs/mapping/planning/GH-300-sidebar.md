# GH-300 sidebar slices 1–2

Issue: https://github.com/cogco1/MonkeyHub/issues/300 (approved by the owner on 2026-09-25).
Base: `e183a69a`. Branch `codex/300-sidebar-process-fold`, after GH-285/process-fold on the same branch.

- Footer: 7-day model usage read from MonkeyMonitor, and "新版本已就绪" when `/api/updates/status`
  reports `ready`, which opens Settings › Software Update. Settings and Archived chats stay.
- "任务" / "Tasks": running and queued Agent work across open projects, from the runtime snapshot the
  Hub already receives; an item opens its project and chat. Project rows carry a running badge. The
  "N new" schemes badge is a marked hook until #294's admission data exists.
- Rail (owner decision, 2026-09-25): "工作面" / "Surfaces" holds 建模 and 画板; "工具" / "Tools" holds
  图纸, 渲染, 制作 and 用量. 排版 is a mode of 画板, reached from a "画板 | 排版" switch.

Later slices (header search and notifications, "待你决定", Library, More) are not part of this lane.
