/**
 * One candidate run, watched to the end and then read back.
 *
 * The job is polled because the job is what answers "is it over"; the candidate
 * readout is only meaningful once it is. A run the runner refused is a *failed
 * job carrying the runner's own sentence*, not an HTTP error, so that sentence
 * is printed verbatim rather than turned into a status word.
 *
 * The three-state chips are the server's counts; `harness` is printed as it
 * came, because it is the line that says what kind of run these numbers are
 * from.
 */

import { useEffect, useState } from "react";

import { asStudioApiError, studio } from "../../api/client";
import { ErrorPanel } from "../../app/ErrorPanel";
import { RelationChips } from "../../app/RelationChips";
import { sha8 } from "../../app/format";
import { IN_FLIGHT } from "../../app/jobs";
import {
  failed,
  idle,
  loading,
  ready,
  type Loadable,
} from "../../app/loadable";
import type {
  CandidateDto,
  JobDto,
  ProjectArtifactDto,
} from "../../api/generated";
import { HonestyLines } from "../state/HonestyLines";
import { candidateSourceLabel } from "../artifacts/ArtifactList";

/** How often the job is asked whether it is over. */
const POLL_MS = 400;

export function CandidatePanel({
  candidateId,
  jobId,
  loadingSha,
  onJobStatus,
  onOpenArtifact,
}: {
  candidateId: string;
  jobId: string;
  loadingSha: string | null;
  onJobStatus(candidateId: string, status: string): void;
  onOpenArtifact(artifact: ProjectArtifactDto, sourceLabel: string): void;
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
        if (!cancelled) setCandidate(ready(value));
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
        await readCandidate();
      } catch (cause) {
        if (!cancelled) setJob(failed(asStudioApiError(cause)));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // The watch is identified by the run it watches; `onJobStatus` is the
    // shell's stable reporter, so listing it changes nothing at runtime and
    // keeps the dependency list honest.
  }, [candidateId, jobId, onJobStatus]);

  return (
    <div className="candidate">
      <p className="panel__subhead mono">{candidateId}</p>
      {job.status === "loading" && <p className="panel__note">reading job…</p>}
      {job.status === "failed" && (
        <ErrorPanel error={job.error} what={`GET /api/jobs/${jobId}`} />
      )}
      {job.status === "ready" && (
        <>
          <div className="chips">
            <span
              className={`chip ${
                job.value.status === "succeeded"
                  ? "chip--held"
                  : job.value.status === "failed"
                    ? "chip--violated"
                    : "chip--unchecked"
              }`}
            >
              job {job.value.status}
            </span>
            {job.value.wallTimeS !== null && (
              <span className="chip chip--neutral">
                {job.value.wallTimeS.toFixed(2)} s
              </span>
            )}
          </div>
          {job.value.error && (
            <p className="panel__note panel__note--refused mono">
              {job.value.error}
            </p>
          )}
          <p className="panel__note">{job.value.persistence}</p>
        </>
      )}

      {candidate.status === "loading" && (
        <p className="panel__note">reading the candidate's records…</p>
      )}
      {candidate.status === "failed" && (
        <ErrorPanel
          error={candidate.error}
          what={`GET /api/candidates/${candidateId}`}
        />
      )}
      {candidate.status === "ready" && (
        <CandidateReadout
          candidate={candidate.value}
          loadingSha={loadingSha}
          onOpenArtifact={onOpenArtifact}
        />
      )}
    </div>
  );
}

function CandidateReadout({
  candidate,
  loadingSha,
  onOpenArtifact,
}: {
  candidate: CandidateDto;
  loadingSha: string | null;
  onOpenArtifact(artifact: ProjectArtifactDto, sourceLabel: string): void;
}) {
  return (
    <div className="candidate__readout">
      <RelationChips checks={candidate.relationChecks} />
      <div className="chips">
        <span
          className={`chip ${
            candidate.seatExecutionComplete ? "chip--held" : "chip--unchecked"
          }`}
        >
          seatExecutionComplete {String(candidate.seatExecutionComplete)}
        </span>
        <span className="chip chip--neutral">
          changedVsProjection {String(candidate.changedVsProjection)}
        </span>
      </div>
      <dl className="facts">
        <dt>status</dt>
        <dd className="mono">{candidate.status}</dd>
        <dt>proposal</dt>
        <dd className="mono">{candidate.proposalId}</dd>
        <dt>base</dt>
        <dd className="mono">
          v{candidate.base.version} · {sha8(candidate.base.stateSha256)}
        </dd>
        <dt>state digest</dt>
        <dd className="mono">{candidate.stateDigest ?? "—"}</dd>
        <dt>record digest</dt>
        <dd className="mono">{candidate.recordDigest ?? "—"}</dd>
        <dt>receipt</dt>
        <dd className="mono">{candidate.receiptRef}</dd>
        <dt>wall time</dt>
        <dd className="mono">
          {candidate.wallTimeS === null ? "—" : `${candidate.wallTimeS} s`}
        </dd>
      </dl>

      <p className="panel__subhead">
        seat results ({candidate.seatResults.length})
      </p>
      {candidate.seatResults.length === 0 ? (
        <p className="panel__note">the receipt names no seats</p>
      ) : (
        <ul className="rows">
          {candidate.seatResults.map((seat) => (
            <li key={seat.seatId} className="row row--static">
              <span className="row__id mono">{seat.seatId}</span>
              <span className="row__meta">{seat.status}</span>
              <span className="row__meta mono">
                {seat.programRef ?? "no program ref"}
              </span>
              <span className="row__meta">
                {seat.objects === null ? "no object count" : `${seat.objects} objects`}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p className="panel__subhead">
        exported artifacts ({candidate.artifacts.length})
      </p>
      {candidate.artifacts.length === 0 ? (
        <p className="panel__note">
          this candidate exported no model. A `.3dm` is a file that was written,
          never a claim that anything passed — and its absence is not a failure.
        </p>
      ) : (
        <ul className="rows">
          {candidate.artifacts.map((artifact) => (
            <li key={artifact.artifactId}>
              {/* A candidate's export is an artifact like any other: the same
                  digest-addressed bytes route serves it, so it opens in the
                  same viewer under its own source chip. */}
              <button
                type="button"
                className={`row${artifact.available ? "" : " is-unavailable"}`}
                disabled={!artifact.available || loadingSha !== null}
                onClick={() =>
                  onOpenArtifact(
                    artifact,
                    candidateSourceLabel(candidate.candidateId),
                  )
                }
              >
                <span className="row__id">{artifact.fileName}</span>
                <span className="row__meta mono">{sha8(artifact.sha256)}</span>
                <span className="row__meta">
                  {artifact.available
                    ? "available"
                    : (artifact.unavailableReason ?? "unavailable")}
                </span>
                {loadingSha !== null && loadingSha === artifact.sha256 && (
                  <span className="row__fields">loading bytes…</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {candidate.skippedRuns.length > 0 && (
        <p className="panel__note panel__note--refused mono">
          skipped runs: {candidate.skippedRuns.join(", ")}
        </p>
      )}

      <p className="panel__note">{candidate.harness}</p>
      <HonestyLines lines={candidate.honesty} />
    </div>
  );
}
