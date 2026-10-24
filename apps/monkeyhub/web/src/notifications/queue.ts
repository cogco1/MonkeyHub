/**
 * The attention queue as this window holds it (1-4): the unsettled cards across projects and their counts,
 * read from the Hub's `/api/attention` and read again whenever the Hub's event stream says the queue changed.
 * The attention shell lists the cards and the notices raise news from them; both read this one copy.
 *
 * A Hub that does not serve the queue yet (its routes join the Hub after 0-2) answers 404: the queue is then
 * unavailable, and the notices keep reading the conversations as before.
 */

import type { AttentionCard, AttentionSummary, CardActionResult, CardState } from "./cards/types";

/** What the Hub publishes on its event stream after any change to the queue. */
export const ATTENTION_CHANGED = "attention/changed";

export interface QueueSnapshot {
  /** null until the first reading; false when this Hub does not serve the queue. */
  readonly available: boolean | null;
  readonly cards: readonly AttentionCard[];
  readonly summary: AttentionSummary | null;
}

export interface QueueFailure {
  readonly code: string;
  readonly detail: string;
}

let snapshot: QueueSnapshot = { available: null, cards: [], summary: null };
const listeners = new Set<() => void>();
let reading = false, again = false;

const UNSERVED = new Set([404, 405]);

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) }, ...init });
  if (UNSERVED.has(response.status)) throw Object.assign(new Error("unserved"), { unserved: true });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw { code: typeof body?.code === "string" ? body.code : "HTTP_ERROR", detail: typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}` } satisfies QueueFailure;
  }
  return body as T;
}

function publish(next: QueueSnapshot): void {
  snapshot = next;
  for (const listener of listeners) listener();
}

/** Reads the queue again; a reading asked for while one runs follows it once. */
export async function refreshQueue(): Promise<void> {
  if (reading) { again = true; return; }
  reading = true;
  try {
    const [cards, summary] = await Promise.all([call<AttentionCard[]>("/api/attention"), call<AttentionSummary>("/api/attention/summary")]);
    publish({ available: true, cards, summary });
  } catch (cause) {
    // A Hub without the queue says so once; a reading that failed otherwise keeps what was read last.
    if ((cause as { unserved?: boolean }).unserved) publish({ available: false, cards: [], summary: null });
  } finally {
    reading = false;
    if (again) { again = false; void refreshQueue(); }
  }
}

/** The Hub's event stream named a change: read the queue again, if this Hub serves it. */
export function queueChanged(kind: string): void {
  if (kind === ATTENTION_CHANGED && snapshot.available !== false) void refreshQueue();
}

export function subscribeQueue(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1 && snapshot.available === null) void refreshQueue();
  return () => { listeners.delete(listener); };
}

export const queueSnapshot = (): QueueSnapshot => snapshot;

/** Cards in the given states, for a page the queue itself does not hold (handled, set aside). */
export function cardsIn(states: readonly CardState[], projectDir?: string): Promise<AttentionCard[]> {
  const query = new URLSearchParams();
  for (const state of states) query.append("state", state);
  if (projectDir) query.set("projectDir", projectDir);
  return call<AttentionCard[]>(`/api/attention?${query}`);
}

const post = <T>(path: string, body?: unknown) => call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const acted = async <T>(action: Promise<T>): Promise<T> => {
  try { return await action; } finally { void refreshQueue(); }
};

/** The person's answer; the Hub's executor carries it out as them. */
export const resolveCard = (id: string, optionId: string) =>
  acted(post<CardActionResult>(`/api/attention/${encodeURIComponent(id)}/resolve`, { optionId }));
export const undoCard = (id: string) => acted(post<CardActionResult>(`/api/attention/${encodeURIComponent(id)}/undo`));
export const laterCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/later`));
export const dismissCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/dismiss`));
export const reopenCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/reopen`));
