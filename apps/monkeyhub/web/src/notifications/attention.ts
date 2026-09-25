/**
 * The Hub's attention layer (#300, UX review FN-2): which conversation needs
 * the architect, read from the chat summaries the Hub already lists.
 *
 * This module is pure: what changed between two readings of
 * `GET /api/chat/sessions`, which change is worth a notice, the words for it,
 * the short stack of notices on screen, the window title's count and where the
 * stack sits clear of the rail and the composer. The host (AttentionHost.tsx)
 * owns time, the page and the browser. Copy stays local until the catalogs
 * take it (UX review §4 step 6).
 */

import type { ChatSummary } from "../api/generated";
import type { Language } from "../../../../shared-web/src/appearance.js";

export type AttentionKind = "permission" | "finished" | "failed";

/** One moment worth a notice. `key` names the moment: the same one is never announced twice. */
export interface AttentionEvent {
  key: string;
  kind: AttentionKind;
  chatId: string;
  projectDir: string;
  title: string;
}

/** What one reading said about a conversation: only what a transition reads. */
export interface Observed {
  status: NonNullable<ChatSummary["status"]>;
  attention: ChatSummary["attention"];
  error: { code: string; detail: string } | null;
  updatedAt: string;
}

export type Snapshot = ReadonlyMap<string, Observed>;

export function snapshotOf(sessions: readonly ChatSummary[]): Map<string, Observed> {
  return new Map(sessions.map((session) => [session.id, {
    status: session.status ?? "idle", attention: session.attention ?? null,
    error: session.error ? { code: session.error.code, detail: session.error.detail } : null, updatedAt: session.updatedAt,
  }]));
}

/** A turn the architect stopped is their own act, not news. */
const STOPPED = "CHAT_STOPPED";

function failedNow(before: Observed, now: Observed): boolean {
  if (now.error?.code === STOPPED) return false;
  if (before.status === "running" && now.status === "failed") return true;
  // An error the last reading did not have: a turn that failed between two
  // readings, or one the Hub ended. An error that stays is not new.
  return now.error !== null && (before.error === null || before.error.code !== now.error.code || before.error.detail !== now.error.detail);
}

/**
 * The notices one reading raises against the one before it.
 *
 * The first reading is a baseline and raises nothing. A conversation first
 * seen now has no transition to report, except a permission already waiting:
 * that one only exists while its turn runs. `openChatId` is the conversation
 * on screen, if any; its news is in front of the architect already.
 */
export function transitions(previous: Snapshot | null, sessions: readonly ChatSummary[], openChatId: string | null): AttentionEvent[] {
  if (previous === null) return [];
  const events: AttentionEvent[] = [];
  const now = snapshotOf(sessions);
  for (const session of sessions) {
    if (session.id === openChatId) continue;
    const before = previous.get(session.id), after = now.get(session.id)!;
    const add = (kind: AttentionKind) => events.push({ key: `${session.id}:${kind}:${session.updatedAt}`, kind,
      chatId: session.id, projectDir: session.projectDir, title: session.title });
    if (after.attention === "permission" && before?.attention !== "permission") add("permission");
    if (!before) continue;
    if (before.status === "running" && after.status === "idle") add("finished");
    else if (failedNow(before, after)) add("failed");
  }
  return events;
}

/** Remembers a notice's key; false when that moment was announced already. Keeps the last `limit` keys. */
export function remember(seen: Set<string>, key: string, limit = 200): boolean {
  if (seen.has(key)) return false;
  seen.add(key);
  if (seen.size > limit) seen.delete(seen.values().next().value!);
  return true;
}

export const MAX_NOTICES = 3;

/**
 * The stack with one more notice. A conversation holds one notice, its latest;
 * at most three show, and a waiting permission outlasts news that only informs.
 */
export function addNotice(stack: readonly AttentionEvent[], event: AttentionEvent): AttentionEvent[] {
  const next = [...stack.filter((item) => item.chatId !== event.chatId), event];
  while (next.length > MAX_NOTICES) {
    const informing = next.findIndex((item) => item.kind !== "permission");
    next.splice(informing === -1 ? 0 : informing, 1);
  }
  return next;
}

/**
 * The stack after a reading: a permission answered elsewhere and a
 * conversation no longer listed (archived) take their notices with them.
 * The same array comes back when nothing changed.
 */
export function settle(stack: readonly AttentionEvent[], sessions: readonly ChatSummary[]): readonly AttentionEvent[] {
  const listed = new Map(sessions.map((session) => [session.id, session]));
  const kept = stack.filter((item) => {
    const session = listed.get(item.chatId);
    return session !== undefined && (item.kind !== "permission" || session.attention === "permission");
  });
  return kept.length === stack.length ? stack : kept;
}

const TITLE_COUNT = /^\(\d+\) /;

/** The window title with the count of unseen notices in front, or without one at zero. */
export function titled(title: string, count: number): string {
  const base = title.replace(TITLE_COUNT, "");
  return count > 0 ? `(${count}) ${base}` : base;
}

export interface AttentionWords {
  permission: (title: string) => string;
  finished: (title: string) => string;
  failed: (title: string) => string;
  view: string;
  dismiss: string;
  region: string;
  untitled: string;
  systemBody: string;
}

export const attentionCopy: Record<Language, AttentionWords> = {
  "zh-CN": {
    permission: (title) => `「${title}」需要你的授权`, finished: (title) => `「${title}」已完成`, failed: (title) => `「${title}」出错了`,
    view: "查看", dismiss: "关闭通知", region: "通知", untitled: "新对话", systemBody: "点击查看",
  },
  en: {
    permission: (title) => `“${title}” needs your permission`, finished: (title) => `“${title}” is done`,
    failed: (title) => `“${title}” ran into an error`,
    view: "View", dismiss: "Dismiss notification", region: "Notifications", untitled: "New chat", systemBody: "Click to view",
  },
};

/** A conversation's title as a notice names it: trimmed, and short enough for one line. */
export function noticeTitle(title: string, words: AttentionWords, limit = 24): string {
  const characters = Array.from(title.trim().replace(/\s+/g, " "));
  if (!characters.length) return words.untitled;
  return characters.length > limit ? `${characters.slice(0, limit - 1).join("")}…` : characters.join("");
}

export const noticeText = (event: Pick<AttentionEvent, "kind" | "title">, words: AttentionWords) =>
  words[event.kind](noticeTitle(event.title, words));

export interface Box { left: number; top: number; right: number; bottom: number }

/** The widest the stack gets, and the gap it keeps from what it avoids. */
export const STACK_WIDTH = 360;
export const GAP = 16;

/**
 * Where the stack sits, as CSS `right` and `bottom`: bottom-right, left of
 * the rail, and above the composer whenever the two would share columns.
 */
export function clearance(viewport: { width: number; height: number }, rail: Box | null, composer: Box | null): { right: number; bottom: number } {
  const railed = rail !== null && rail.right - rail.left > 0 && rail.left > viewport.width / 2;
  const right = railed ? Math.max(0, viewport.width - rail!.left) + GAP : GAP;
  const stackRight = viewport.width - right;
  const stackLeft = stackRight - Math.min(STACK_WIDTH, Math.max(0, stackRight - GAP));
  const shown = composer !== null && composer.bottom - composer.top > 0 && composer.right - composer.left > 0;
  const shared = shown && composer!.left < stackRight && composer!.right > stackLeft;
  const bottom = shared ? Math.max(GAP, viewport.height - composer!.top + GAP / 2) : GAP;
  return { right, bottom };
}
