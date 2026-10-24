/**
 * The attention queue as this window holds it (1-4): the unsettled cards across projects and their counts,
 * read from the Hub's `/api/attention` and read again whenever the Hub's event stream says the queue changed.
 * The attention shell lists the cards and the notices raise news from them; both read this one copy.
 *
 * A Hub that does not serve the queue yet (its routes join the Hub after 0-2) answers 404: the queue is then
 * unavailable, and the notices keep reading the conversations as before.
 */

import type { AttentionCard, AttentionSummary, BulkResult, CardActionResult, CardState } from "./cards/types";

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
/**
 * Cards this window put in front of the person through their own action: brought back, or raised by their answer.
 * They are not news to the person (useAttention), until a reading has seen them waiting.
 */
export const shownHere = new Set<string>();

let listShown = false;
/** Whether the queue's own list is on screen (the attention shell's Attention page): a card arriving there is seen in it. */
export const showingQueue = (shown: boolean) => { listShown = shown; };
export const queueOnScreen = () => listShown;

// The queue is read again before the action's answer comes back, so a card it raised is there to be shown.
const acted = async <T>(action: Promise<T>, brings: (result: T) => readonly (string | null | undefined)[] = () => []): Promise<T> => {
  try {
    const result = await action;
    for (const id of brings(result)) if (id) shownHere.add(id);
    return result;
  } finally { await refreshQueue(); }
};

/** The person's answer, with their own words where the answer sends a message; the Hub's executor carries it out as them. */
export const resolveCard = (id: string, optionId: string, note?: string) =>
  acted(post<CardActionResult>(`/api/attention/${encodeURIComponent(id)}/resolve`, note === undefined ? { optionId } : { optionId, note }),
    (result) => [result.followUp?.id]);
export const undoCard = (id: string) =>
  acted(post<CardActionResult>(`/api/attention/${encodeURIComponent(id)}/undo`), (result) => [result.item.id, result.followUp?.id]);
export const laterCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/later`));
export const dismissCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/dismiss`));
export const reopenCard = (id: string) => acted(post<AttentionCard>(`/api/attention/${encodeURIComponent(id)}/reopen`), (item) => [item.id]);

/** "All as recommended" over the cards the person has in view, taken back as one. */
export const bulkRecommended = (ids: readonly string[]) => acted(post<BulkResult>("/api/attention/bulk", { ids, mode: "recommended" }));
export const undoBulk = (bulkId: string) =>
  acted(post<BulkResult>(`/api/attention/bulk/${encodeURIComponent(bulkId)}/undo`), (result) => result.items.map((item) => item.id));

let conversationServed = true;
/**
 * The project's attention conversation (1-2), where the new shell's input goes while no conversation is open:
 * the Hub opens it on first need. null where this Hub does not have it yet, which is then asked no more.
 */
export async function attentionConversation(projectDir: string): Promise<string | null> {
  if (!conversationServed) return null;
  try {
    return (await post<{ sessionId: string }>("/api/attention/conversation", { projectDir })).sessionId;
  } catch (cause) {
    if ((cause as { unserved?: boolean }).unserved) conversationServed = false;
    else throw cause;
    return null;
  }
}
