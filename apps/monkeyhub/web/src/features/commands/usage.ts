/**
 * How often each command was used on this machine, and which ones last. Kept in the
 * browser's local storage only: it orders Ctrl+K's recent rows and, during the move to
 * the new shell, says which old entries are still reached for. It holds command ids,
 * counts and how each was reached — never what the command acted on.
 */

export type UseSource = "palette" | "tools" | "shortcut" | "button" | "composer" | "card";

export interface CommandUsage {
  readonly counts: Readonly<Record<string, Readonly<Partial<Record<UseSource, number>>>>>;
  /** Most recent first. */
  readonly recent: readonly string[];
}

export const USAGE_KEY = "monkeyhub.commands.usage@1";
export const RECENT_KEPT = 20;

type Store = Pick<Storage, "getItem" | "setItem">;

const EMPTY: CommandUsage = { counts: {}, recent: [] };

export function readUsage(store: Store | null | undefined): CommandUsage {
  try {
    const saved: unknown = JSON.parse(store?.getItem(USAGE_KEY) ?? "null");
    if (!saved || typeof saved !== "object") return EMPTY;
    const { counts, recent } = saved as Partial<CommandUsage>;
    return {
      counts: counts && typeof counts === "object" ? counts : {},
      recent: Array.isArray(recent) ? recent.filter((id): id is string => typeof id === "string").slice(0, RECENT_KEPT) : [],
    };
  } catch {
    return EMPTY;
  }
}

/** The usage after one more use of `id`. */
export function used(usage: CommandUsage, id: string, source: UseSource): CommandUsage {
  const before = usage.counts[id] ?? {};
  return {
    counts: { ...usage.counts, [id]: { ...before, [source]: (before[source] ?? 0) + 1 } },
    recent: [id, ...usage.recent.filter((other) => other !== id)].slice(0, RECENT_KEPT),
  };
}

/** Record one use; a store that refuses (private window, full quota) only loses the count. */
export function recordUse(store: Store | null | undefined, id: string, source: UseSource): CommandUsage {
  const next = used(readUsage(store), id, source);
  try { store?.setItem(USAGE_KEY, JSON.stringify(next)); } catch { /* the count is a convenience */ }
  return next;
}

export function timesUsed(usage: CommandUsage, id: string): number {
  return Object.values(usage.counts[id] ?? {}).reduce((sum, count) => sum + (count ?? 0), 0);
}
