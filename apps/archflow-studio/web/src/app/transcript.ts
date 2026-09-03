/**
 * The conversation, as this tab remembers it.
 *
 * An append-only list of entries: what you said, and what the server answered
 * — a typed proposal, a question, a refusal, a candidate run, a verdict. Each
 * entry holds the DTO or the error exactly as it arrived; the cards decide how
 * to show it, and nothing here rewrites a server sentence into a summary.
 *
 * One entry is edited after the fact: a candidate's, whose job status changes
 * while it runs. Everything else is history, and history is not version
 * history — this list lives in React state and is dropped with the tab.
 */

import { useCallback, useRef, useState } from "react";

import type { StudioApiError } from "../api/client";
import type { ProposalDto } from "../api/generated";

export type Entry =
  | { kind: "system"; id: string; text: string }
  | { kind: "you"; id: string; text: string }
  | { kind: "proposal"; id: string; proposal: ProposalDto }
  | { kind: "question"; id: string; error: StudioApiError; utterance: string }
  | { kind: "refusal"; id: string; error: StudioApiError; what: string }
  | {
      kind: "candidate";
      id: string;
      candidateId: string;
      jobId: string;
      proposalId: string;
      /** The server's own word for the job: queued, running, succeeded, failed. */
      status: string;
    }
  | { kind: "verdict"; id: string; candidateId: string };

/** An entry before the transcript names it. */
export type EntryDraft =
  | Omit<Extract<Entry, { kind: "system" }>, "id">
  | Omit<Extract<Entry, { kind: "you" }>, "id">
  | Omit<Extract<Entry, { kind: "proposal" }>, "id">
  | Omit<Extract<Entry, { kind: "question" }>, "id">
  | Omit<Extract<Entry, { kind: "refusal" }>, "id">
  | Omit<Extract<Entry, { kind: "candidate" }>, "id">
  | Omit<Extract<Entry, { kind: "verdict" }>, "id">;

export interface Transcript {
  readonly entries: readonly Entry[];
  /** Append one entry; answers the id it was given. */
  append(draft: EntryDraft): string;
  /** Update the candidate entry's job status in place, when it changed. */
  noteJobStatus(candidateId: string, status: string): void;
  hasVerdictFor(candidateId: string): boolean;
}

export function useTranscript(): Transcript {
  const [entries, setEntries] = useState<readonly Entry[]>([]);
  const counter = useRef(0);

  const append = useCallback((draft: EntryDraft): string => {
    counter.current += 1;
    const id = `e${counter.current}`;
    setEntries((current) => [...current, { ...draft, id } as Entry]);
    return id;
  }, []);

  const noteJobStatus = useCallback((candidateId: string, status: string) => {
    setEntries((current) =>
      current.map((entry) =>
        entry.kind === "candidate" &&
        entry.candidateId === candidateId &&
        entry.status !== status
          ? { ...entry, status }
          : entry,
      ),
    );
  }, []);

  const hasVerdictFor = useCallback(
    (candidateId: string) =>
      entries.some(
        (entry) => entry.kind === "verdict" && entry.candidateId === candidateId,
      ),
    [entries],
  );

  return { entries, append, noteJobStatus, hasVerdictFor };
}
