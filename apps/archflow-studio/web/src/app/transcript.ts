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
import type { AgentReadingDto, CompareDto, ProposalDto } from "../api/generated";

export type Entry =
  | { kind: "system"; id: string; text: string }
  | {
      /** The waiting half of a proposal: an agent is reading the sentence. */
      kind: "reading";
      id: string;
      subject: string;
      recordSize: string;
      /** Who reads here, as GET /api/project said: deterministic, codex, anthropic, unknown. */
      provider: string;
      startedAt: number;
    }
  | { kind: "you"; id: string; text: string }
  | {
      kind: "proposal";
      id: string;
      proposal: ProposalDto;
      /** Who read the sentence and what it compiled; the agent's words, kept apart. */
      agent: AgentReadingDto | null;
      /**
       * How many times the hand refined this proposal after the sentence: each
       * refinement is a new proposal in the server's store that replaced this
       * entry's, so the transcript stays one card per intent.
       */
      refinements: number;
    }
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
  | {
      kind: "verdict";
      id: string;
      candidateId: string;
      /** What the sentence asked to keep, for the card's Protected line. */
      protectedRefs: readonly string[];
    }
  | {
      kind: "compare";
      id: string;
      /** Before / After / Why, as the server counted it from the records. */
      comparison: CompareDto;
    };

/** An entry before the transcript names it. */
export type EntryDraft =
  | Omit<Extract<Entry, { kind: "system" }>, "id">
  | Omit<Extract<Entry, { kind: "reading" }>, "id">
  | Omit<Extract<Entry, { kind: "you" }>, "id">
  | Omit<Extract<Entry, { kind: "proposal" }>, "id">
  | Omit<Extract<Entry, { kind: "question" }>, "id">
  | Omit<Extract<Entry, { kind: "refusal" }>, "id">
  | Omit<Extract<Entry, { kind: "candidate" }>, "id">
  | Omit<Extract<Entry, { kind: "verdict" }>, "id">
  | Omit<Extract<Entry, { kind: "compare" }>, "id">;

export interface Transcript {
  readonly entries: readonly Entry[];
  /** Append one entry; answers the id it was given. */
  append(draft: EntryDraft): string;
  /** Drop one entry, for the waiting lines that an answer replaces. */
  remove(entryId: string): void;
  /** Update the candidate entry's job status in place, when it changed. */
  noteJobStatus(candidateId: string, status: string): void;
  /**
   * Replace a proposal entry's proposal with a refinement of it, counting the
   * refinement. An entry that is not a proposal is left alone.
   */
  replaceProposal(entryId: string, proposal: ProposalDto, agent: AgentReadingDto | null): void;
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

  const remove = useCallback((entryId: string) => {
    setEntries((current) => current.filter((entry) => entry.id !== entryId));
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

  const replaceProposal = useCallback(
    (entryId: string, proposal: ProposalDto, agent: AgentReadingDto | null) => {
      setEntries((current) =>
        current.map((entry) =>
          entry.kind === "proposal" && entry.id === entryId
            ? { ...entry, proposal, agent, refinements: entry.refinements + 1 }
            : entry,
        ),
      );
    },
    [],
  );

  const hasVerdictFor = useCallback(
    (candidateId: string) =>
      entries.some(
        (entry) => entry.kind === "verdict" && entry.candidateId === candidateId,
      ),
    [entries],
  );

  return { entries, append, remove, noteJobStatus, replaceProposal, hasVerdictFor };
}
