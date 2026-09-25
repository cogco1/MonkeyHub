/**
 * The attention layer's clock (#300): reads the chat list, turns what changed
 * into notices, and, while the window is hidden, into OS notifications and a
 * count in the window title. Rules live in attention.ts; this file owns time,
 * the page and the browser.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { chatSessionsApiChatSessionsGet, type ChatSummary } from "../api/generated";
import { createClient } from "../api/generated/client";
import type { Language } from "../../../../shared-web/src/appearance.js";
import { addNotice, attentionCopy, noticeText, remember, settle, snapshotOf, titled, transitions, type AttentionEvent, type Snapshot } from "./attention";
import { openChat } from "./openChat";

/** How often the chat list is read. A reading never overlaps the one before it. */
export const POLL_MS = 4000;
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
  const baseline = useRef<Snapshot | null>(null);
  const seen = useRef(new Set<string>());
  const system = useRef(new Set<Notification>());
  const asked = useRef(false);

  const dismiss = useCallback((notice: AttentionEvent) => {
    setNotices((stack) => stack.some((item) => item.key === notice.key) ? stack.filter((item) => item.key !== notice.key) : stack);
  }, []);
  const open = useCallback((notice: AttentionEvent) => {
    dismiss(notice);
    openChat(notice.chatId, notice.projectDir);
  }, [dismiss]);
  const openRef = useRef(open);
  openRef.current = open;

  useEffect(() => {
    let stopped = false, reading = false, again = false, timer: number | undefined;
    /** One OS notification for a moment the architect cannot see; permission is asked for once, ever. */
    const notify = (event: AttentionEvent) => {
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
    };
    const read = async () => {
      if (stopped) return;
      if (reading) { again = true; return; }
      reading = true;
      window.clearTimeout(timer);
      try {
        const sessions = await readSessions();
        if (stopped) return;
        const onScreen = shown();
        const raised = transitions(baseline.current, sessions, onScreen ? openChatId() : null)
          .filter((event) => remember(seen.current, event.key));
        baseline.current = snapshotOf(sessions);
        setNotices((stack) => raised.reduce<readonly AttentionEvent[]>((next, event) => addNotice(next, event), settle(stack, sessions)));
        if (!onScreen && raised.length) {
          setUnseen((count) => count + raised.length);
          for (const event of raised) notify(event);
        }
      } catch {
        // The Hub did not answer this time; the next reading asks again.
      } finally {
        reading = false;
        if (!stopped) {
          if (again) { again = false; void read(); } else timer = window.setTimeout(() => void read(), POLL_MS);
        }
      }
    };
    const onVisibility = () => {
      const onScreen = shown();
      setVisible(onScreen);
      if (!onScreen) return;
      // Back on screen: the count is seen, the OS copies are no longer
      // needed, and the conversation shown speaks for itself.
      setUnseen(0);
      for (const note of system.current) note.close();
      system.current.clear();
      const current = openChatId();
      setNotices((stack) => stack.some((item) => item.chatId === current) ? stack.filter((item) => item.chatId !== current) : stack);
      void read();
    };
    void read();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => { document.title = titled(document.title, unseen); }, [unseen]);
  useEffect(() => () => { document.title = titled(document.title, 0); }, []);

  return { notices, visible, open, dismiss };
}
