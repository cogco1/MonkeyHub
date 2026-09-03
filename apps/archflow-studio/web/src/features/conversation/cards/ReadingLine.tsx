/**
 * The waiting half of a proposal, on screen: an agent is reading the
 * sentence against the record, and this is how long it has been at it.
 *
 * The line exists because the slowest thing in the product was one word on
 * a button. It names what is happening and counts; it claims nothing about
 * the answer. The proposal card, the question or the refusal replaces it.
 */

import { useEffect, useState } from "react";

export function ReadingLine({
  subject,
  recordSize,
  startedAt,
}: {
  subject: string;
  recordSize: string;
  startedAt: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, (now - startedAt) / 1000);
  return (
    <p className="reading" aria-live="polite">
      <span>
        an agent is reading your sentence against the record ({recordSize}) for{" "}
        <span className="mono">{subject}</span>…
      </span>
      <span className="mono reading__clock">{seconds.toFixed(0)} s</span>
    </p>
  );
}
