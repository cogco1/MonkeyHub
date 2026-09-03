/**
 * The waiting half of a proposal, on screen: who is reading the sentence
 * against the record, and how long they have been at it.
 *
 * The line exists because the slowest thing in the product was one word on
 * a button. It names what is happening and counts; it claims nothing about
 * the answer, and it names the reader the server said it has - the grammar
 * alone when no agent is wired - never an agent by assumption. The proposal
 * card, the question or the refusal replaces it.
 */

import { useEffect, useState } from "react";

/** Who is reading, in words, from the provider GET /api/project named. */
export function readerWords(provider: string): string {
  switch (provider) {
    case "deterministic":
      return "the studio is typing your sentence against the record";
    case "codex":
    case "anthropic":
      return `${provider} is reading your sentence against the record`;
    default:
      return "your sentence is being read against the record";
  }
}

export function ReadingLine({
  subject,
  recordSize,
  provider,
  startedAt,
}: {
  subject: string;
  recordSize: string;
  provider: string;
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
        {readerWords(provider)} ({recordSize}) for{" "}
        <span className="mono">{subject}</span>…
      </span>
      <span className="mono reading__clock">{seconds.toFixed(0)} s</span>
    </p>
  );
}
