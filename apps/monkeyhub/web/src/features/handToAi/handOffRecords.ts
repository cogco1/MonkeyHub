/**
 * S08: each hand-over to the AI, kept as the record of that opinion. The marks themselves leave the surface
 * once handed over; the message and its attachments stay in the conversation, and this list, shown on the
 * History page, says when, from where and in which conversation. It is this browser's index of those
 * messages, by project folder, like the composer's unsent drafts; the conversation holds the content.
 */
import type { HandToAiSurface } from "./handToAi";

export interface HandOffRecord {
  readonly id: string;
  readonly at: string;
  readonly projectDir: string;
  readonly surface: HandToAiSurface;
  readonly words: string;
  readonly marks: number;
  readonly thumbnail: string | null;
  readonly chatId: string;
}

const KEY = "monkeyhub.hand-offs";
const LIMIT = 50;
export const HAND_OFFS_CHANGED = "monkeyhub:hand-offs";

function readAll(): Record<string, HandOffRecord[]> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return saved && typeof saved === "object" && !Array.isArray(saved) ? saved as Record<string, HandOffRecord[]> : {};
  } catch { return {}; }
}

/** This project's hand-overs, newest first. */
export function readHandOffs(projectDir: string): HandOffRecord[] {
  const rows = readAll()[projectDir];
  return Array.isArray(rows) ? rows.filter((row) => row && typeof row.id === "string" && typeof row.chatId === "string") : [];
}

/** Keep one more; a full browser store drops the pictures first, then keeps nothing rather than failing the hand-over. */
export function rememberHandOff(record: HandOffRecord): void {
  const all = readAll();
  const rows = [record, ...(all[record.projectDir] ?? [])].slice(0, LIMIT);
  for (const next of [rows, rows.map((row) => ({ ...row, thumbnail: null }))]) {
    try {
      localStorage.setItem(KEY, JSON.stringify({ ...all, [record.projectDir]: next }));
      window.dispatchEvent(new CustomEvent(HAND_OFFS_CHANGED, { detail: { projectDir: record.projectDir } }));
      return;
    } catch { /* try again without pictures */ }
  }
}
