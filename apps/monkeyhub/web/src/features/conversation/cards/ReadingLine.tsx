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
import { useT, type TFunction } from "../../../i18n/useT";

/** Who is reading, in words, from the provider GET /api/project named. */
export function readerWords(provider: string, t: TFunction): string {
  switch (provider) {
    case "deterministic":
      return t("reading.deterministic");
    case "codex":
    case "anthropic":
      return t("reading.agent", { provider });
    default:
      return t("reading.default");
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
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, (now - startedAt) / 1000);
  return (
    <p className="reading" aria-live="polite">
      <span>
        {readerWords(provider, t)} (<span className="mono">{recordSize}</span>){" "}
        {t("reading.for")} {" "}
        <span className="mono">{subject}</span>…
      </span>
      <span className="mono reading__clock">{seconds.toFixed(0)} s</span>
    </p>
  );
}
