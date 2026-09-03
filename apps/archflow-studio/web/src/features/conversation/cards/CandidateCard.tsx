/**
 * One candidate run, watched to the end and then read back.
 *
 * The job is polled because the job is what answers "is it over"; the candidate
 * readout is only meaningful once it is. A run the runner refused is a *failed
 * job carrying the runner's own sentence*, not an HTTP error, so that sentence
 * is printed verbatim rather than turned into a status word. `harness` is
 * printed as it came, because it is the line that says what kind of run these
 * numbers are from.
 */

import { useEffect, useState } from "react";

import { asStudioApiError, studio } from "../../../api/client";
import type {
  CandidateDto,
  JobDto,
  ProjectArtifactDto,
} from "../../../api/generated";
import { ErrorPanel } from "../../../app/ErrorPanel";
import type { EvidenceTab } from "../../../app/evidence";
import { IN_FLIGHT } from "../../../app/jobs";
import {
  failed,
  idle,
  loading,
  ready,
  type Loadable,
} from "../../../app/loadable";
import { candidateSourceLabel } from "../../artifacts/artifactLabels";
import { Verbatim } from "./Verbatim";

/** How often the job is asked whether it is over. */
const POLL_MS = 400;

export function CandidateCard({
  candidateId,
  jobId,
  loadingSha,
  onJobStatus,
  onCandidate,
  onPreview,
  onEvidence,
}: {
  candidateId: string;
  jobId: string;
  loadingSha: string | null;
  onJobStatus(candidateId: string, status: string): void;
  onCandidate(candidate: CandidateDto): void;
  onPreview(artifact: ProjectArtifactDto, sourceLabel: string): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const [job, setJob] = useState<Loadable<JobDto>>(idle);
  const [candidate, setCandidate] = useState<Loadable<CandidateDto>>(idle);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    setJob(loading);
    setCandidate(idle);

    const readCandidate = async () => {
      setCandidate(loading);
      try {
        const value = await studio.candidate(candidateId);
        if (cancelled) return;
        setCandidate(ready(value));
        onCandidate(value);
      } catch (cause) {
        if (!cancelled) setCandidate(failed(asStudioApiError(cause)));
      }
    };

    const tick = async () => {
      try {
        const value = await studio.job(jobId);
        if (cancelled) return;
        setJob(ready(value));
        onJobStatus(candidateId, value.status);
        if (IN_FLIGHT.has(value.status)) {
          timer = window.setTimeout(() => void tick(), POLL_MS);
          return;
        }
        if (value.status === "succeeded") await readCandidate();
      } catch (cause) {
        if (!cancelled) setJob(failed(asStudioApiError(cause)));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // The watch is identified by the run it watches; the two reporters are the
    // shell's stable callbacks.
  }, [candidateId, jobId, onJobStatus, onCandidate]);

  const status = job.status === "ready" ? job.value.status : "reading";
  return (
    <article className="card">
      <div className="card__row">
        <p className="card__title">
          Candidate <span className={`status status--${status}`}>{status}</span>
          {job.status === "ready" && job.value.wallTimeS !== null && (
            <span className="quiet"> {job.value.wallTimeS.toFixed(1)} s</span>
          )}
        </p>
        <p className="quiet mono">{candidateId}</p>
      </div>
      {job.status === "failed" && (
        <div className="card__row">
          <ErrorPanel error={job.error} what={`GET /api/jobs/${jobId}`} />
        </div>
      )}
      {job.status === "ready" && job.value.error && (
        <div className="card__row">
          <p className="verbatim-line">{job.value.error}</p>
        </div>
      )}
      {candidate.status === "loading" && (
        <div className="card__row">
          <p className="quiet">reading the candidate's records…</p>
        </div>
      )}
      {candidate.status === "failed" && (
        <div className="card__row">
          <ErrorPanel
            error={candidate.error}
            what={`GET /api/candidates/${candidateId}`}
          />
        </div>
      )}
      {candidate.status === "ready" && (
        <CandidateReadout
          candidate={candidate.value}
          loadingSha={loadingSha}
          onPreview={onPreview}
          onEvidence={onEvidence}
        />
      )}
    </article>
  );
}

function CandidateReadout({
  candidate,
  loadingSha,
  onPreview,
  onEvidence,
}: {
  candidate: CandidateDto;
  loadingSha: string | null;
  onPreview(artifact: ProjectArtifactDto, sourceLabel: string): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const seats = candidate.seatResults;
  return (
    <>
      <div className="card__row">
        <p>
          {seats.length} seat{seats.length === 1 ? "" : "s"}
          {seats.length > 0 && " · "}
          {seats
            .map(
              (seat) =>
                `${seat.seatId} ${seat.status}` +
                (seat.objects !== null ? ` · ${seat.objects} objects` : ""),
            )
            .join(" · ")}
        </p>
        <p className="quiet">{candidate.harness}</p>
        {!candidate.seatExecutionComplete && (
          <p className="quiet">seat execution is not complete</p>
        )}
        {/* Where the seconds went, as the receipt times them: the run, then
            each export with the runner's own word for its path. A run that
            exported nothing says so with no export figures at all. */}
        <p className="quiet mono">
          {candidate.timings.runS === null
            ? "run time not recorded"
            : `run ${candidate.timings.runS.toFixed(1)} s`}
          {candidate.timings.exports.length > 0 &&
            " · export " +
              candidate.timings.exports
                .map(
                  (item) =>
                    `${item.seconds === null ? "?" : item.seconds.toFixed(1)} s (${item.path ?? "path unknown"}` +
                    (item.rebuildRatio !== null
                      ? `, rebuilt ${item.rebuiltObjects} of ${(item.rebuiltObjects ?? 0) + (item.keptObjects ?? 0)}`
                      : "") +
                    ")",
                )
                .join(" + ")}
        </p>
      </div>
      <div className="card__row">
        {candidate.artifacts.length === 0 ? (
          <p className="quiet">
            this candidate exported no model — a file that was not written, not
            a failure
          </p>
        ) : (
          <div className="actions">
            {candidate.artifacts.map((artifact) =>
              artifact.available ? (
                <button
                  key={artifact.artifactId}
                  type="button"
                  className="btn"
                  disabled={loadingSha !== null}
                  onClick={() =>
                    onPreview(
                      artifact,
                      candidateSourceLabel(candidate.candidateId),
                    )
                  }
                >
                  {loadingSha !== null && loadingSha === artifact.sha256
                    ? "loading bytes…"
                    : `Preview ${artifact.fileName}`}
                </button>
              ) : (
                <span key={artifact.artifactId} className="quiet">
                  {artifact.fileName}:{" "}
                  {artifact.unavailableReason ??
                    "unavailable, and the server gave no reason"}
                </span>
              ),
            )}
          </div>
        )}
        {candidate.skippedRuns.length > 0 && (
          <p className="quiet mono">
            skipped runs: {candidate.skippedRuns.join(", ")}
          </p>
        )}
      </div>
      {candidate.honesty.length > 0 && (
        <div className="card__row">
          <Verbatim lines={candidate.honesty} />
        </div>
      )}
      <div className="card__row actions">
        <button
          type="button"
          className="btn btn--link"
          onClick={() => onEvidence("receipts")}
        >
          receipts
        </button>
      </div>
    </>
  );
}
