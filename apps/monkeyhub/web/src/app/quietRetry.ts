/**
 * How often a failed read is tried again by itself (F046, 03 4-3), kept by place: AutoRetry.tsx draws it. Pure, so
 * tests load it directly.
 */
export const QUIET_RETRIES = [1000, 3000] as const;
/** Quiet tries count again from zero once a place has not failed for this long. */
const FORGET_MS = 30_000;

// The tries are kept by place, not by button: the button is usually gone while its read runs again, and a new one
// would otherwise start over and retry for ever.
export const tried = new Map<string, { count: number; at: number }>();

/** Quiet tries are on unless this browser turned them off (QUIET_KEY = "off"), as tests that press each retry do. */
export const QUIET_KEY = "monkeyhub.quietRetry";
const quiet = () => { try { return localStorage.getItem(QUIET_KEY) !== "off"; } catch { return true; } };

/** The delay before the next quiet try at this place, or null once the person should be asked. */
export function nextQuietTry(place: string, now = Date.now()): number | null {
  if (typeof localStorage !== "undefined" && !quiet()) return null;
  const row = tried.get(place);
  const count = row && now - row.at < FORGET_MS ? row.count : 0;
  if (count >= QUIET_RETRIES.length) return null;
  tried.set(place, { count: count + 1, at: now });
  return QUIET_RETRIES[count]!;
}
