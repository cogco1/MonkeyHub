# GH-300 attention

Issue: https://github.com/cogco1/MonkeyHub/issues/300
Base: `e7fd526e` (batch D claim).

A Hub-wide attention layer: a toast when a chat needs permission, finishes or fails while another view is open, an OS notification and a title count while the window is hidden, and a chat summary flag for pending permission.

Batch E (2026-09-25 afternoon), parallel with batch D; none touches App.tsx, ChatShell.tsx or the i18n catalogs.

## What the lane delivers

- **Summary flag** (`models.py`, `chat.py`). `ChatSummary.attention` is `"permission"` exactly while a message still carries its permission request, the request the conversation shows with options, and `null` otherwise. It is derived on every read from the record in memory and excluded from the saved record. `GET /api/chat/sessions` copies only the summary fields. A permission request and its answer each move `updatedAt`.
- **Rules** (`web/src/notifications/attention.ts`, pure). Transitions between two readings: a permission became pending; `running` became `idle`; a turn became `failed` or gained a new error, but not `CHAT_STOPPED`. The first reading is a baseline. A chat seen for the first time raises only a permission already waiting. A moment is keyed `chatId:kind:updatedAt`, and the last 200 keys are remembered. The stack holds one notice per chat and at most three, and permissions outlast informing notices. Also here: the title count, the words, and the stack's placement.
- **Clock** (`useAttention.ts`). The hook reads `GET /api/chat/sessions` through the generated client 4 s after each answer, so readings never overlap. It keeps reading while the window is hidden, because the hidden-window notices depend on it, and reads at once when the window returns. When the window is visible, the chat named by `?chatId=` raises nothing.
- **Notices** (`AttentionHost.tsx`, `attention.css`). They sit bottom-right in one polite live region, left of the rail. They rise above the composer whenever the two would share columns, which is measured and followed as the composer grows. A permission stays until it is opened, dismissed or answered elsewhere. The others fade 8 s after they are on screen, and pointer or focus holds them. Escape dismisses. Reduced motion is respected. A Hub page framed inside another page (Fabrication in the Hub) mounts none.
- **Hidden window**. Each moment sends one OS notification, tagged by its key. Permission is asked once, ever, at the first hidden moment, and nothing happens if it is unavailable or denied. Clicking a notification focuses the window and opens the chat. The title reads `(n) MonkeyHub`. When the window is seen again, the count resets, the OS copies close and the open chat's notice goes.
- **Opening a chat** (`openChat.ts`). 查看 dispatches `monkeyhub:open-chat` (`detail: {chatId, projectDir}`, cancelable). If no listener calls `preventDefault()` within 300 ms, the Hub loads `?chatId=` without `runtimeId` or `view`.

## Words (local until the catalogs take them)

| Kind | zh-CN | en |
| --- | --- | --- |
| permission | 「标题」需要你的授权 · 查看 | “Title” needs your permission · View |
| finished | 「标题」已完成 · 查看 | “Title” is done · View |
| failed | 「标题」出错了 · 查看 | “Title” ran into an error · View |

Other words: 关闭通知 / Dismiss notification (×), 通知 / Notifications (region), 新对话 / New chat (empty title) and 点击查看 / Click to view (OS body). Titles longer than 24 characters are shortened with an ellipsis.

## Handoff: ChatShell listener (root, at integration)

ChatShell opens the chat in place: it finds the session (or uses `projectDir`), calls `event.preventDefault()`, then `selectChat`. Without the listener the reload fallback works, but it loses unsent composer text.

## Tests

- `apps/monkeyhub/api/tests/test_chat_attention.py`: the flag through the real ACP fake agent. It covers answer, stop, restart after a crash, the HTTP list and detail, `updatedAt` moving, and no file reads while listing.
- `apps/monkeyhub/web/test/attention.test.ts`: transitions, dedupe, the stack, the title, the words and placement.
- `apps/monkeyhub/web/test/notifications.browser.mjs`: the built Hub on Playwright's clock, with fixture routes, a stubbed `Notification` and emulated visibility.

## Not in this slice

- The listener in `ChatShell.tsx`, and a "needs you" mark on thread and project rows (FN-2's second half). `ChatShell.tsx` belongs to GH-285 today.
- Moving the words into the catalogs (UX review §4 step 6).
- Reading the runtime SSE instead of polling.
