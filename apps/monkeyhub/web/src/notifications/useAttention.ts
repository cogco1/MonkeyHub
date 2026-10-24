/**
 * The attention layer's clock (#300): turns what changed into notices and, while the window is hidden, into
 * OS notifications and a count in the window title. The chat list is read and its changes are news. Where the
 * Hub serves the attention queue (1-4), a card that comes to need the person is news too, read whenever the
 * Hub's event stream says the queue changed (queue.ts); a waiting permission is then one of those cards, so
 * the conversations only say when a turn finished or failed. Rules live in attention.ts; this file owns time,
 * the page and the browser.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { chatSessionsApiChatSessionsGet, type ChatSummary } from "../api/generated";
import { createClient } from "../api/generated/client";
import type { Language } from "../../../../../packages/web-shared/src/appearance.js";
import {
  addNotice, attentionCopy, cardNews, noticeText, pendingCards, remember, settle, settleCards, snapshotOf, titled, transitions,
  withoutChatPermissions, type AttentionEvent, type Snapshot,
} from "./attention";
import { openCard, openChat } from "./openChat";
import { queueSnapshot, shownHere, subscribeQueue } from "./queue";

/** How often the chat list is read while a turn runs or waits. A reading never overlaps the one before it. */
export const POLL_MS = 4000;
/**
 * How often it is read while every conversation is quiet. A turn this window
 * starts is still noticed within POLL_MS: the architect's own input brings the
 * next reading forward to POLL_MS after it, and a window being hidden reads the
 * list at once, so a turn that finishes behind it is not missed.
 */
export const QUIET_POLL_MS = 30_000;
/** Set once the OS has been asked to allow notifications: it is asked once, ever. */
const ASKED_KEY = "monkeyhub.attention.systemAsked";

const client = createClient({ baseUrl: window.location.origin });

async function readSessions(): Promise<ChatSummary[]> {
  const result = await chatSessionsApiChatSessionsGet({ client });
  if (!result.response?.ok || result.data === undefined) throw new Error(`HTTP ${result.response?.status ?? 0}`);
  return result.data;
}

/** The conversation this window shows, as its address says. */
const openChatId = () => new URLSearchParams(window.location.search).get("chatId") || null;
const shown = () => document.visibilityState === "visible";

function askedBefore(): boolean {
  try { return localStorage.getItem(ASKED_KEY) !== null; } catch { return false; }
}
function markAsked(): void {
  try { localStorage.setItem(ASKED_KEY, new Date().toISOString()); } catch { /* This window remembers it below. */ }
}

export interface Attention {
  notices: readonly AttentionEvent[];
  /** Whether the window is on screen; an informing notice only fades while it is. */
  visible: boolean;
  open: (notice: AttentionEvent) => void;
  dismiss: (notice: AttentionEvent) => void;
}

export function useAttention(language: Language): Attention {
  const [notices, setNotices] = useState<readonly AttentionEvent[]>([]);
  const [unseen, setUnseen] = useState(0);
  const [visible, setVisible] = useState(shown);
  const languageRef = useRef(language);
  languageRef.current = language;
  const seen = useRef(new Set<string>());
  const system = useRef(new Set<Notification>());
  const asked = useRef(false);
  const queue = useSyncExternalStore(subscribeQueue, queueSnapshot);
  const fromQueue = queue.available === true;
  const fromQueueRef = useRef(fromQueue);
  fromQueueRef.current = fromQueue;

  const dismiss = useCallback((notice: AttentionEvent) => {
    setNotices((stack) => stack.some((item) => item.key === notice.key) ? stack.filter((item) => item.key !== notice.key) : stack);
  }, []);
  const open = useCallback((notice: AttentionEvent) => {
    dismiss(notice);
    if (notice.card) openCard(notice.card.id, notice.chatId, notice.projectDir);
    else openChat(notice.chatId, notice.projectDir);
  }, [dismiss]);
  const openRef = useRef(open);
  openRef.current = open;

  /** One OS notification for a moment the architect cannot see; permission is asked for once, ever. */
  const notify = useCallback((event: AttentionEvent) => {
    if (typeof Notification === "undefined") return;
    const post = () => {
      try {
        const words = attentionCopy[languageRef.current];
        // The tag folds the same moment from two Hub windows into one notification.
        const note = new Notification(noticeText(event, words), { body: words.systemBody, tag: event.key, lang: languageRef.current });
        system.current.add(note);
        note.onclose = () => { system.current.delete(note); };
        note.onclick = () => { window.focus(); note.close(); openRef.current(event); };
      } catch { /* This window cannot show OS notifications; the notice and the title count remain. */ }
    };
    if (Notification.permission === "granted") { post(); return; }
    if (Notification.permission !== "default" || asked.current || askedBefore()) return;
    asked.current = true;
    markAsked();
    void Promise.resolve(Notification.requestPermission())
      .then((answer) => { if (answer === "granted" && !shown()) post(); }, () => undefined);
  }, []);

  /** News from either source, once each; the stack first settled against what was read. */
  const raise = useCallback((raised: readonly AttentionEvent[], settled: (stack: readonly AttentionEvent[]) => readonly AttentionEvent[]) => {
    const fresh = raised.filter((event) => remember(seen.current, event.key));
    setNotices((stack) => fresh.reduce<readonly AttentionEvent[]>((next, event) => addNotice(next, event), settled(stack)));
    if (!shown() && fresh.length) {
      setUnseen((count) => count + fresh.length);
      for (const event of fresh) notify(event);
    }
  }, [notify]);

  // The queue, where the Hub serves it: each reading against the one before.
  const cardBaseline = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!fromQueue) { cardBaseline.current = null; return; }
    // A card the person's own action brought up here is in front of them already.
    raise(cardNews(cardBaseline.current, queue.cards, shownHere), (stack) => settleCards(withoutChatPermissions(stack), queue.cards));
    cardBaseline.current = pendingCards(queue.cards);
    for (const id of [...shownHere]) if (cardBaseline.current.has(id)) shownHere.delete(id);
  }, [fromQueue, queue, raise]);

  // The conversations, read with or without the queue.
  useEffect(() => {
    let stopped = false, reading = false, again = false, timer: number | undefined, dueAt = 0, busy = false;
    let baseline: Snapshot | null = null;
    /** The next reading, `ms` from now. */
    const schedule = (ms: number) => {
      window.clearTimeout(timer);
      dueAt = Date.now() + ms;
      timer = window.setTimeout(() => void read(), ms);
    };
    const read = async () => {
      if (stopped) return;
      if (reading) { again = true; return; }
      reading = true;
      window.clearTimeout(timer);
      try {
        const sessions = await readSessions();
        if (stopped) return;
        busy = sessions.some((session) => session.status === "running" || session.attention === "permission");
        const queued = fromQueueRef.current;
        const raised = transitions(baseline, sessions, shown() ? openChatId() : null).filter((event) => !queued || event.kind !== "permission");
        baseline = snapshotOf(sessions);
        raise(raised, (stack) => queued ? withoutChatPermissions(settle(stack, sessions)) : settle(stack, sessions));
      } catch {
        // The Hub did not answer this time; the next reading asks again.
      } finally {
        reading = false;
        if (!stopped) {
          if (again) { again = false; void read(); } else schedule(busy ? POLL_MS : QUIET_POLL_MS);
        }
      }
    };
    // Hidden: read now, so a turn already under way is followed at POLL_MS while nobody looks. Back on screen: read too.
    const onVisibility = () => void read();
    // Input comes before what it sends, so it brings the next reading forward instead of reading now.
    const onInput = () => { if (!reading && dueAt - Date.now() > POLL_MS) schedule(POLL_MS); };
    void read();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pointerdown", onInput, true);
    window.addEventListener("keydown", onInput, true);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointerdown", onInput, true);
      window.removeEventListener("keydown", onInput, true);
    };
  }, [raise]);

  // Back on screen: the count is seen, the OS copies are no longer needed, and the conversation shown speaks for itself.
  useEffect(() => {
    const onVisibility = () => {
      const onScreen = shown();
      setVisible(onScreen);
      if (!onScreen) return;
      setUnseen(0);
      for (const note of system.current) note.close();
      system.current.clear();
      const current = openChatId();
      setNotices((stack) => stack.some((item) => !item.card && item.chatId === current) ? stack.filter((item) => item.card || item.chatId !== current) : stack);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => { document.title = titled(document.title, unseen); }, [unseen]);
  useEffect(() => () => { document.title = titled(document.title, 0); }, []);

  return { notices, visible, open, dismiss };
}
