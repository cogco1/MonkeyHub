# GH-300 needs-you

Issue: https://github.com/cogco1/MonkeyHub/issues/300
Base: `acb391ef` (batch DE).

The shell shows what needs the architect: rows waiting on a permission are marked, pressing the active rail entry steps back one level everywhere, opening a tool shows its skeleton with named steps and a cancel, and project and Stage stay visible on narrow screens.

Batch F (2026-09-25 afternoon); none touches App.tsx, the i18n catalogs (GH-244) or GH-66's files.

## Landed in the needs-you lane

- **FN-2, needs you.** A thread whose summary has `attention: "permission"` carries a filled 需要你 / Needs you pill, and its button is described as 需要你的授权 / Needs your permission. Its project row carries a smaller outlined 需要你 mark in place of Running, and the project button's description adds "N 个对话需要你". Both are the rows' existing buttons, so no new tab stop; the marks leave as soon as the permission is answered.
- **IA-6.** One rule for every rail entry: the entry on screen steps back one level when pressed. A surface (Modeling, Board with Layout, Design Tree) leaves the panel; a Tool (Drawings, Render, Fabrication, Usage) returns to the surface that was on screen when it opened, through any Tool opened in between, or leaves the panel when it was opened from the conversation. The entry says what a press does (`aria-description`, title). The Hide/Show tools toggle stays.
- **PP-1.** A tool that is not ready at once shows its surface's skeleton after 120 ms, with the surface's name and the step the start path is on: 正在连接项目… / 正在启动项目服务… / 正在打开模型… (正在启动服务… for Fabrication). After 3 s it shows 已等待 N秒 and 取消. Cancel, or pressing the entry again, returns to what was on screen; the service keeps starting and the next open reuses it. A tool that fails returns there too, with the failure in the conversation. The composer no longer says "正在连接项目…" for a tool; a send still does while it connects.
- **NA-2.** Below 900 px an open panel starts under the chat header, which keeps the project name, the conversation and the sidebar toggle in view. The Stage chip is the project surface's own bar chip while one is open; otherwise the header carries it, in the chip's words, and opens the Design Tree. Exactly one chip is on screen. `ProjectWorkspace` reports the chip's words as `WorkspacePosition.chip`.
- **GH-58, unrecoverable operations.** A `needs_recovery` row with `recoverable: false` shows 无法自动恢复 beside 知道了, which acknowledges it through the existing route; once acknowledged it leaves the notice. Rows without the field keep today's rule.

New copy lives in `shellWords` in `ChatShell.tsx` until the catalogs are free.

## Open

- Real rows carry `recoverable` once GH-58 lands and the Hub client is regenerated; until then the local `OperationRow` type reads it.
- The Hide/Show tools toggle is now a second way to leave the panel (IA-2, #283).
- Tasks still says 进行中 for a turn that waits on a permission (`sidebarTasks.ts`, outside this lane).
- The collapsed sidebar and the closed narrow sidebar show no needs-you mark; the notices announce it.
- The chip's words are computed in `DesignTreeBar` and again in `ProjectWorkspace` until `features/designTree/words.ts` offers them.
- After the skeleton, a surface's own boot overlay (`LoadingOverlay`) still shows while its runtime answers (R14, DC-8).
- Runtimes without a Design Tree have no Stage chip to keep in view; the header names the project only.
