/** Candidate reads belong to the shell, so closing the conversation cannot stop them. */
import { useCallback, useEffect, useRef, useState } from "react";

import { asStudioApiError, studio } from "../api/client";
import type { CandidateDto, JobDto, ValidationDto } from "../api/generated";
import { IN_FLIGHT } from "./jobs";
import { failed, idle, loading, ready, type Loadable } from "./loadable";
import { startClientTiming, type ClientTimingSpan } from "./clientTiming";

export interface CandidateReadback {
  readonly job: Loadable<JobDto>;
  readonly candidate: Loadable<CandidateDto>;
  readonly validation: Loadable<ValidationDto>;
}

export const EMPTY_CANDIDATE_READBACK: CandidateReadback = {
  job: idle, candidate: idle, validation: idle,
};

interface CandidateWatch {
  refresh(): void;
  stop(): void;
}

export function useCandidateRuns(
  entries: readonly { candidateId: string; jobId: string }[],
  onJobStatus: (candidateId: string, status: string) => void,
  diagnostics?: { timingFor(candidateId: string): ClientTimingSpan | null; onReadFailure(candidateId: string): void },
) {
  const [runs, setRuns] = useState<Readonly<Record<string, CandidateReadback>>>({});
  const watches = useRef(new Map<string, CandidateWatch>());
  const reporter = useRef(onJobStatus);
  reporter.current = onJobStatus;
  const diagnostic = useRef(diagnostics);
  diagnostic.current = diagnostics;

  useEffect(() => {
    for (const { candidateId, jobId } of entries) {
      if (watches.current.has(candidateId)) continue;
      let stopped = false;
      let readingJob = false;
      let readingCandidate = false;
      let readingValidation = false;
      let candidateRead = false;
      let validationRead = false;
      let jobFailures = 0;
      let candidateFailures = 0;
      let jobTimer: number | undefined;
      let candidateTimer: number | undefined;
      const update = (value: Partial<CandidateReadback>) => {
        if (!stopped) setRuns((current) => ({ ...current,
          [candidateId]: { ...(current[candidateId] ?? EMPTY_CANDIDATE_READBACK), ...value },
        }));
      };

      const readCandidate = async () => {
        if (stopped || candidateRead || readingCandidate) return;
        readingCandidate = true;
        update({ candidate: loading });
        const parent = diagnostic.current?.timingFor(candidateId);
        const timing = parent && !parent.closed ? startClientTiming("api_wait",
          { ...parent.binding, runId: candidateId }, parent.trace,
          { request_kind: "candidate_read", retry_attempt: candidateFailures }) : null;
        try {
          const value = await studio.candidate(candidateId, timing?.trace);
          timing?.finish(stopped ? "cancelled" : "succeeded");
          if (stopped) return;
          candidateRead = true;
          update({ candidate: ready(value) });
        } catch (cause) {
          timing?.finish(stopped ? "cancelled" : "failed");
          update({ candidate: failed(asStudioApiError(cause)) });
          if (!stopped && ++candidateFailures < 3) {
            candidateTimer = window.setTimeout(() => void readCandidate(), 500 * candidateFailures);
          } else if (!stopped) diagnostic.current?.onReadFailure(candidateId);
        } finally { readingCandidate = false; }
      };

      const readValidation = async () => {
        if (stopped || validationRead || readingValidation) return;
        readingValidation = true;
        update({ validation: loading });
        try {
          const value = await studio.validation(candidateId);
          if (stopped) return;
          validationRead = true;
          update({ validation: ready(value) });
        } catch (cause) {
          update({ validation: failed(asStudioApiError(cause)) });
        } finally { readingValidation = false; }
      };

      const tick = async () => {
        if (stopped || readingJob) return;
        readingJob = true;
        const parent = diagnostic.current?.timingFor(candidateId);
        const timing = parent && !parent.closed ? startClientTiming("api_wait",
          { ...parent.binding, runId: candidateId }, parent.trace,
          { request_kind: "candidate_poll", retry_attempt: jobFailures }) : null;
        try {
          const value = await studio.job(jobId, timing?.trace);
          timing?.finish(stopped ? "cancelled" : "succeeded");
          if (stopped) return;
          jobFailures = 0;
          update({ job: ready(value) });
          reporter.current(candidateId, value.status);
          if (IN_FLIGHT.has(value.status)) {
            jobTimer = window.setTimeout(() => void tick(), 400);
          } else if (value.status === "succeeded") {
            // Geometry can be shown while the independent review is still running.
            void readCandidate();
            void readValidation();
          }
        } catch (cause) {
          timing?.finish(stopped ? "cancelled" : "failed");
          update({ job: failed(asStudioApiError(cause)) });
          if (!stopped && ++jobFailures < 3) {
            jobTimer = window.setTimeout(() => void tick(), 500 * jobFailures);
          } else if (!stopped) diagnostic.current?.onReadFailure(candidateId);
        } finally { readingJob = false; }
      };

      const watch = {
        refresh() {
          window.clearTimeout(jobTimer);
          window.clearTimeout(candidateTimer);
          jobFailures = 0;
          candidateFailures = 0;
          void tick();
        },
        stop() {
          stopped = true;
          window.clearTimeout(jobTimer);
          window.clearTimeout(candidateTimer);
        },
      };
      watches.current.set(candidateId, watch);
      update({ job: loading });
      void tick();
    }
    const present = new Set(entries.map((entry) => entry.candidateId));
    for (const [id, watch] of watches.current) {
      if (!present.has(id)) { watch.stop(); watches.current.delete(id); }
    }
  }, [entries]);

  useEffect(() => {
    const active = watches.current;
    return () => { for (const watch of active.values()) watch.stop(); active.clear(); };
  }, []);

  const refresh = useCallback((candidateId: string) => {
    watches.current.get(candidateId)?.refresh();
  }, []);
  return { runs, refresh };
}
