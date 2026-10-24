/**
 * The Hub's attention layer (#300, UX review FN-2): what needs the architect. Where the Hub serves the
 * attention queue (1-4), a card that comes to need the person is the news; where it does not yet, the news is
 * read from the chat summaries the Hub already lists.
 *
 * This module is pure: what changed between two readings of the queue or of
 * `GET /api/chat/sessions`, which change is worth a notice, the words for it,
 * the short stack of notices on screen, the window title's count and where the
 * stack sits clear of what the shells and workspaces register (regions.ts). The
 * host (AttentionHost.tsx) owns time, the page and the browser. Copy stays local
 * until the catalogs take it (UX review §4 step 6).
 */

import type { ChatSummary } from "../api/generated";
import type { Language } from "../../../../../packages/web-shared/src/appearance.js";
import type { AttentionCard, AttentionChannel } from "./cards/types";

export type AttentionKind = "permission" | "finished" | "failed" | "card";

/** One moment worth a notice. `key` names the moment: the same one is never announced twice. */
export interface AttentionEvent {
  key: string;
  kind: AttentionKind;
  /** The conversation it is about; for a card, the one it came from, or "". */
  chatId: string;
  projectDir: string;
  title: string;
  /** For a card's notice: the card, and its title in Chinese. */
  card?: { id: string; channel: AttentionChannel; titleZh: string | null };
}

/** A card that needs the person now: open, or a change list waiting for review (cards/cardText.ts, PENDING). */
const isPending = (card: Pick<AttentionCard, "state">) => card.state === "open" || card.state === "review";

/** What a notice is about: one notice each, its latest. */
export const noticeSubject = (event: Pick<AttentionEvent, "chatId" | "card">) => event.card ? `card:${event.card.id}` : event.chatId;

/** A notice that waits for the person instead of fading: a permission, or a card that asks something of them. */
export const lasts = (event: Pick<AttentionEvent, "kind" | "card">) =>
  event.kind === "permission" || (event.kind === "card" && event.card?.channel !== "notice");

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
 * The stack with one more notice. A conversation or a card holds one notice,
 * its latest; at most three show, and what waits for the person outlasts news
 * that only informs.
 */
export function addNotice(stack: readonly AttentionEvent[], event: AttentionEvent): AttentionEvent[] {
  const next = [...stack.filter((item) => noticeSubject(item) !== noticeSubject(event)), event];
  while (next.length > MAX_NOTICES) {
    const informing = next.findIndex((item) => !lasts(item));
    next.splice(informing === -1 ? 0 : informing, 1);
  }
  return next;
}

/**
 * The stack after a reading of the conversations: a permission answered
 * elsewhere and a conversation no longer listed (archived) take their notices
 * with them. Card notices follow the queue instead. The same array comes back
 * when nothing changed.
 */
export function settle(stack: readonly AttentionEvent[], sessions: readonly ChatSummary[]): readonly AttentionEvent[] {
  const listed = new Map(sessions.map((session) => [session.id, session]));
  const kept = stack.filter((item) => {
    if (item.card) return true;
    const session = listed.get(item.chatId);
    return session !== undefined && (item.kind !== "permission" || session.attention === "permission");
  });
  return kept.length === stack.length ? stack : kept;
}

/** The stack after a reading of the queue: a card answered, put off or set aside takes its notice with it. */
export function settleCards(stack: readonly AttentionEvent[], cards: readonly AttentionCard[]): readonly AttentionEvent[] {
  const waiting = new Set(cards.filter(isPending).map((card) => card.id));
  const kept = stack.filter((item) => !item.card || waiting.has(item.card.id));
  return kept.length === stack.length ? stack : kept;
}

/**
 * The notices one reading of the queue raises: each card that needs the person
 * now and did not at the reading before. The first reading is a baseline and
 * raises nothing; a card the person is looking at already raises nothing either.
 */
export function cardNews(previous: ReadonlySet<string> | null, cards: readonly AttentionCard[], onScreen: ReadonlySet<string> = new Set()): AttentionEvent[] {
  if (previous === null) return [];
  return cards.filter((card) => isPending(card) && !previous.has(card.id) && !onScreen.has(card.id)).map((card) => ({
    key: `card:${card.id}:${card.updatedAt}`, kind: "card" as const, chatId: card.source.chatSessionId ?? "", projectDir: card.projectDir,
    title: card.title, card: { id: card.id, channel: card.channel, titleZh: card.titleZh ?? null },
  }));
}

/** The cards that need the person in a reading, for the next one to compare against. */
export const pendingCards = (cards: readonly AttentionCard[]): Set<string> => new Set(cards.filter(isPending).map((card) => card.id));

const TITLE_COUNT = /^\(\d+\) /;

/** The window title with the count of unseen notices in front, or without one at zero. */
export function titled(title: string, count: number): string {
  const base = title.replace(TITLE_COUNT, "");
  return count > 0 ? `(${count}) ${base}` : base;
}

export interface AttentionWords {
  language: Language;
  permission: (title: string) => string;
  finished: (title: string) => string;
  failed: (title: string) => string;
  /** A card's notice, by the channel it belongs to. */
  card: Record<AttentionChannel, (title: string) => string>;
  view: string;
  dismiss: string;
  region: string;
  untitled: string;
  systemBody: string;
}

export const attentionCopy: Record<Language, AttentionWords> = {
  "zh-CN": {
    language: "zh-CN",
    permission: (title) => `「${title}」需要你的授权`, finished: (title) => `「${title}」已完成`, failed: (title) => `「${title}」出错了`,
    card: { decide: (title) => `要你决定：${title}`, annotate: (title) => `等你在图上看：${title}`, guess: (title) => `想问你：${title}`, notice: (title) => title },
    view: "查看", dismiss: "关闭通知", region: "通知", untitled: "新对话", systemBody: "点击查看",
  },
  en: {
    language: "en",
    permission: (title) => `“${title}” needs your permission`, finished: (title) => `“${title}” is done`,
    card: { decide: (title) => `Your decision: ${title}`, annotate: (title) => `For you on the drawing: ${title}`, guess: (title) => `A question for you: ${title}`,
      notice: (title) => title },
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

export function noticeText(event: Pick<AttentionEvent, "kind" | "title" | "card">, words: AttentionWords): string {
  if (event.kind !== "card" || !event.card) return words[event.kind as Exclude<AttentionKind, "card">](noticeTitle(event.title, words));
  // A card's title is written as a sentence to read; it is shortened, never quoted.
  const title = words.language === "zh-CN" && event.card.titleZh ? event.card.titleZh : event.title;
  return words.card[event.card.channel](noticeTitle(title, words, 60));
}

export interface Box { left: number; top: number; right: number; bottom: number }
/** A registered region's box, and the edge of the window it keeps the stack from. */
export interface EdgeBox extends Box { edge: "right" | "bottom" }

/** The widest the stack gets, and the gap it keeps from what it avoids. */
export const STACK_WIDTH = 360;
export const GAP = 16;

/**
 * Where the stack sits, as CSS `right` and `bottom`: bottom-right, left of
 * whatever stands at the right edge in the window's right half (a rail, a side
 * panel), and above whatever lies along the bottom (a composer, a floating
 * toolbar) whenever the two would share columns.
 */
export function clearance(viewport: { width: number; height: number }, regions: readonly EdgeBox[]): { right: number; bottom: number } {
  const railed = regions.filter((box) => box.edge === "right" && box.right - box.left > 0 && box.left > viewport.width / 2);
  const right = railed.length ? Math.max(...railed.map((box) => Math.max(0, viewport.width - box.left))) + GAP : GAP;
  const stackRight = viewport.width - right;
  const stackLeft = stackRight - Math.min(STACK_WIDTH, Math.max(0, stackRight - GAP));
  const below = regions.filter((box) => box.edge === "bottom" && box.bottom - box.top > 0 && box.right - box.left > 0
    && box.left < stackRight && box.right > stackLeft);
  const bottom = below.length ? Math.max(GAP, ...below.map((box) => viewport.height - box.top + GAP / 2)) : GAP;
  return { right, bottom };
}
