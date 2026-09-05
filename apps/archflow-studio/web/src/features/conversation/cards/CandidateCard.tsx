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
import { artifactKindKey, isViewable } from "../../artifacts/artifactSelection";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";
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
  labelOf,
}: {
  candidateId: string;
  jobId: string;
  loadingSha: string | null;
  onJobStatus(candidateId: string, status: string): void;
  onCandidate(candidate: CandidateDto): void;
  onPreview(artifact: ProjectArtifactDto, sourceLabel: string): void;
  onEvidence(tab: EvidenceTab, candidateId?: string): void;
  /** The sentence another candidate of this tab was made from, for naming a blocker. */
  labelOf(candidateId: string): string | null;
}) {
  const t = useT();
  const [job, setJob] = useState<Loadable<JobDto>>(idle);
  const [candidate, setCandidate] = useState<Loadable<CandidateDto>>(idle);
  // A clock while the run is in flight: eighty seconds of one word was the
  // review's complaint. The seconds are this browser's; the receipt's
  // timings replace them when the run is over.
  const [now, setNow] = useState(() => Date.now());
  const inFlight = job.status === "ready" && IN_FLIGHT.has(job.value.status);
  useEffect(() => {
    if (!inFlight) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [inFlight]);
  const since =
    job.status === "ready" ? (job.value.startedAt ?? job.value.createdAt) : null;
  const elapsed = since ? Math.max(0, (now - Date.parse(since)) / 1000) : null;

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
          {t("candidate.title")} {" "}
          <span className={`status status--${status}`}>
            {job.status === "ready"
              ? job.value.status
              : t("candidate.status.reading")}
          </span>
          {job.status === "ready" && job.value.wallTimeS !== null && (
            <span className="quiet"> {job.value.wallTimeS.toFixed(1)} s</span>
          )}
          {inFlight && elapsed !== null && (
            <span className="quiet mono"> {elapsed.toFixed(0)} s</span>
          )}
        </p>
        <p className="quiet mono">{candidateId}</p>
      </div>
      {job.status === "ready" && job.value.status === "queued" && job.value.waitingReason && (
        <div className="card__row">
          <p className="quiet">
            {job.value.waitingFor ? (
              <>
                {t("candidate.waitingFor")}{" "}
                <span
                  className={labelOf(job.value.waitingFor) ? "" : "mono"}
                  title={job.value.waitingFor}
                >
                  {labelOf(job.value.waitingFor)
                    ? `“${labelOf(job.value.waitingFor)}”`
                    : job.value.waitingFor}
                </span>{" "}
                · <BilingualText source={job.value.waitingReason} />
              </>
            ) : (
              <>
                {t("candidate.waiting")} ·{" "}
                <BilingualText source={job.value.waitingReason} />
              </>
            )}
          </p>
        </div>
      )}
      {job.status === "ready" && job.value.status === "running" && (
        <div className="card__row">
          <p className="quiet">
            {job.value.lane === "exclusive"
              ? t("candidate.running.exportLane")
              : t("candidate.running.kernelOnly")}
          </p>
        </div>
      )}
      {job.status === "failed" && (
        <div className="card__row">
          <ErrorPanel error={job.error} what={`GET /api/jobs/${jobId}`} />
        </div>
      )}
      {job.status === "ready" && job.value.error && (
        <div className="card__row">
          <p className="verbatim-line">
            <BilingualText source={job.value.error} />
          </p>
        </div>
      )}
      {candidate.status === "loading" && (
        <div className="card__row">
          <p className="quiet">{t("candidate.readingRecords")}</p>
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
  onEvidence(tab: EvidenceTab, candidateId?: string): void;
}) {
  const t = useT();
  const seats = candidate.seatResults;
  return (
    <>
      <div className="card__row">
        <p>
          {t(
            seats.length === 1
              ? "candidate.seats.one"
              : "candidate.seats.many",
            { count: seats.length },
          )}
          {seats.map((seat) => (
            <span key={seat.seatId}>
              {" · "}
              <span className="mono">{seat.seatId}</span>{" "}
              <span className="mono">{seat.status}</span>
              {seat.objects !== null && (
                <> · {t("candidate.objects", { count: seat.objects })}</>
              )}
            </span>
          ))}
        </p>
        <p className="quiet">
          <BilingualText source={candidate.harness} />
        </p>
        {!candidate.seatExecutionComplete && (
          <p className="quiet">{t("candidate.seatExecutionIncomplete")}</p>
        )}
        {/* Where the seconds went, as the receipt times them: the run, then
            each export with the runner's own word for its path. A run that
            exported nothing says so with no export figures at all. */}
        <p className="quiet mono">
          {candidate.timings.runS === null
            ? t("candidate.timings.runUnrecorded")
            : (
                <>
                  {t("candidate.timings.run")} {candidate.timings.runS.toFixed(1)} s
                </>
              )}
          {candidate.timings.exports.length > 0 && (
            <>
              {" · "}
              {t("candidate.timings.export")} {" "}
              {candidate.timings.exports.map((item, index) => (
                <span key={`${item.path ?? "unknown"}:${index}`}>
                  {index > 0 && " + "}
                  {item.seconds === null ? "?" : item.seconds.toFixed(1)} s ({
                    item.path === null ? (
                      t("candidate.timings.pathUnknown")
                    ) : (
                      <span>{item.path}</span>
                    )
                  }
                  {item.rebuildRatio !== null && (
                    <>
                      , {t("candidate.timings.rebuilt")} {item.rebuiltObjects} {" "}
                      {t("candidate.timings.of")} {" "}
                      {(item.rebuiltObjects ?? 0) + (item.keptObjects ?? 0)}
                    </>
                  )}
                  )
                </span>
              ))}
            </>
          )}
        </p>
      </div>
      <div className="card__row">
        {candidate.artifacts.length === 0 ? (
          <p className="quiet">
            {t("candidate.noModelExported")}
          </p>
        ) : (
          <div className="actions">
            {candidate.artifacts.map((artifact) =>
              // Only a 3dm goes to the viewer: the Rhino export, or the mesh
              // preview an in-process export writes beside its exact STEP.
              artifact.available && isViewable(artifact) ? (
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
                    ? t("candidate.loadingBytes")
                    : (
                        <>
                          {t("candidate.preview")} {" "}
                          <span className="mono">{artifact.fileName}</span>
                        </>
                      )}
                </button>
              ) : null,
            )}
            {candidate.artifacts.map((artifact) =>
              artifact.available && artifact.sha256 ? (
                <a
                  key={`save-${artifact.artifactId}`}
                  className="btn btn--link"
                  href={`/api/artifacts/${artifact.sha256}/bytes`}
                  download={artifact.fileName}
                  title={t("candidate.saveCertifiedTitle")}
                >
                  {t("common.save")} {" "}
                  <span className="mono">{artifact.fileName}</span>
                  {" "}· {t(artifactKindKey(artifact))}
                </a>
              ) : (
                <span key={artifact.artifactId} className="quiet">
                  <span className="mono">{artifact.fileName}</span>
                  {" "}({t(artifactKindKey(artifact))}): {" "}
                  {artifact.unavailableReason ? (
                    <BilingualText source={artifact.unavailableReason} />
                  ) : (
                    t("candidate.unavailableNoReason")
                  )}
                </span>
              ),
            )}
          </div>
        )}
        {candidate.skippedRuns.length > 0 && (
          <p className="quiet mono">
            {t("candidate.skippedRuns")}: {" "}
            <span>{candidate.skippedRuns.join(", ")}</span>
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
          onClick={() => onEvidence("receipts", candidate.candidateId)}
        >
          {t("candidate.receipts")}
        </button>
      </div>
    </>
  );
}
