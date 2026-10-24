import { useEffect, useRef, useState, type ButtonHTMLAttributes } from "react";

import { useT } from "../i18n/useT";

/**
 * A read that failed is tried again by itself (F046, 03 4-3): after a second, then after three; only a failure
 * that stays shows the button. Most failures here pass by themselves (a project service starting, the Hub
 * restarting), and the Hub already sends a request again while the Runtime says so. Saves and other writes keep
 * their own buttons: whether they may go again is the server's to say, not this one's.
 */
export const QUIET_RETRIES = [1000, 3000] as const;
/** Quiet tries count again from zero once a place has not failed for this long. */
const FORGET_MS = 30_000;

// The tries are kept by place, not by button: the button is usually gone while its read runs again, and a new one
// would otherwise start over and retry for ever.
const tried = new Map<string, { count: number; at: number }>();

/** The delay before the next quiet try at this place, or null once the person should be asked. */
export function nextQuietTry(place: string, now = Date.now()): number | null {
  const row = tried.get(place);
  const count = row && now - row.at < FORGET_MS ? row.count : 0;
  if (count >= QUIET_RETRIES.length) return null;
  tried.set(place, { count: count + 1, at: now });
  return QUIET_RETRIES[count]!;
}

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> & {
  /** What failed: shown with this button; a new value is the read failing again. */
  failure: unknown;
  retry: () => unknown;
  /** Where the read is, so its tries are counted across buttons; the button's data-command-arg by default. */
  place?: string;
  "data-command"?: string;
  "data-command-arg"?: string;
};

export function AutoRetry({ failure, retry, place, children, ...button }: Props) {
  const t = useT();
  const latest = useRef(retry);
  latest.current = retry;
  const key = place ?? button["data-command-arg"] ?? "read";
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    if (!failure || button.disabled) { setWaiting(false); return; }
    const delay = nextQuietTry(key);
    if (delay === null) { setWaiting(false); return; }
    setWaiting(true);
    const timer = window.setTimeout(() => { setWaiting(false); latest.current(); }, delay);
    return () => window.clearTimeout(timer);
  }, [failure, key, button.disabled]);
  if (waiting) return <span className="auto-retry" role="status">{t("retry.auto")}</span>;
  return <button type="button" {...button} onClick={() => { tried.delete(key); latest.current(); }}>{children}</button>;
}
