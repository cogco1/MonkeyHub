import { useEffect, useRef, useState, type ButtonHTMLAttributes } from "react";

import { useT } from "../i18n/useT";
import { nextQuietTry, tried } from "./quietRetry";

/**
 * A read that failed is tried again by itself (F046, 03 4-3): after a second, then after three; only a failure
 * that stays shows the button. Most failures here pass by themselves (a project service starting, the Hub
 * restarting), and the Hub already sends a request again while the Runtime says so. Saves and other writes keep
 * their own buttons: whether they may go again is the server's to say, not this one's.
 */
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
  // The command the caller names (a wrapper the entry rules list), carried by the button it renders.
  return <button type="button" {...button} data-command={button["data-command"]} onClick={() => { tried.delete(key); latest.current(); }}>{children}</button>;
}
