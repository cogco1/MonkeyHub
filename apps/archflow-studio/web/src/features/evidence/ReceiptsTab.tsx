/**
 * The selected candidate's records, as the server read them back: the run
 * receipt's seat rows, the exported artifacts with disk's answer, and the
 * kernel's validation receipt unedited.
 */

import type { CandidateDto, ValidationDto } from "../../api/generated";

export function ReceiptsTab({
  candidate,
  validation,
}: {
  candidate: CandidateDto | null;
  validation: ValidationDto | null;
}) {
  if (candidate === null) {
    return <p className="ev__none">no candidate has run in this tab</p>;
  }
  return (
    <>
      <div className="ev">
        <p className="label">Candidate receipt</p>
        <dl>
          <dt>run</dt>
          <dd>{candidate.candidateId}</dd>
          <dt>job</dt>
          <dd>{candidate.jobId} · {candidate.status}</dd>
          <dt>proposal</dt>
          <dd>{candidate.proposalId}</dd>
          <dt>base</dt>
          <dd>
            v{candidate.base.version} · {candidate.base.stateSha256 ?? "—"}
          </dd>
          <dt>state digest</dt>
          <dd>{candidate.stateDigest ?? "—"}</dd>
          <dt>record digest</dt>
          <dd>{candidate.recordDigest ?? "—"}</dd>
          <dt>changed</dt>
          <dd>{String(candidate.changedVsProjection)}</dd>
          <dt>receipt</dt>
          <dd>{candidate.receiptRef}</dd>
          <dt>wall time</dt>
          <dd>{candidate.wallTimeS === null ? "—" : `${candidate.wallTimeS} s`}</dd>
          <dt>seats complete</dt>
          <dd>{String(candidate.seatExecutionComplete)}</dd>
          <dt>harness</dt>
          <dd>{candidate.harness}</dd>
        </dl>
      </div>
      <div className="ev">
        <p className="label">Seat rows ({candidate.seatResults.length})</p>
        {candidate.seatResults.length === 0 ? (
          <p className="ev__none">the receipt names no seats</p>
        ) : (
          <dl>
            {candidate.seatResults.map((seat) => (
              <SeatRow key={seat.seatId} seat={seat} />
            ))}
          </dl>
        )}
      </div>
      <div className="ev">
        <p className="label">Exported artifacts ({candidate.artifacts.length})</p>
        {candidate.artifacts.length === 0 ? (
          <p className="ev__none">no model was exported</p>
        ) : (
          <dl>
            {candidate.artifacts.map((artifact) => (
              <ArtifactRow key={artifact.artifactId} artifact={artifact} />
            ))}
          </dl>
        )}
        {candidate.skippedRuns.length > 0 && (
          <p className="verbatim-line">
            skipped runs: {candidate.skippedRuns.join(", ")}
          </p>
        )}
      </div>
      <div className="ev">
        <p className="label">Validation receipt</p>
        {validation === null ? (
          <p className="ev__none">no verdict has been read for this candidate</p>
        ) : (
          <>
            <dl>
              <dt>receipt</dt>
              <dd>{validation.receipt.receiptId}</dd>
              <dt>submission</dt>
              <dd>
                {validation.receipt.submissionId} · {validation.receipt.submissionDigest}
              </dd>
              <dt>checked state</dt>
              <dd>
                v{validation.receipt.checkedState.version} ·{" "}
                {validation.receipt.checkedState.stateSha256 ?? "—"}
              </dd>
              <dt>passed</dt>
              <dd>{String(validation.receipt.passed)}</dd>
              <dt>validators</dt>
              <dd>{validation.validators.join(" · ")}</dd>
              <dt>effective</dt>
              <dd>
                {validation.effectiveChecks.length === 0
                  ? "none"
                  : validation.effectiveChecks.join(" · ")}
              </dd>
              <dt>advance</dt>
              <dd>{String(validation.advance)}</dd>
              <dt>blocked by</dt>
              <dd>
                {validation.blockedBy.length === 0
                  ? "no clause refused"
                  : validation.blockedBy.join(" · ")}
              </dd>
            </dl>
            <p className="label">Findings ({validation.receipt.findings.length})</p>
            {validation.receipt.findings.length === 0 ? (
              <p className="ev__none">
                the gates returned no findings — the receipt's answer, not a claim
                about what was not checked
              </p>
            ) : (
              <ul>
                {validation.receipt.findings.map((finding, index) => (
                  <li key={`${finding.code}:${index}`}>
                    <span className="mono">{finding.code}</span> · {finding.severity}{" "}
                    · {finding.message}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </>
  );
}

function SeatRow({ seat }: { seat: CandidateDto["seatResults"][number] }) {
  return (
    <>
      <dt>{seat.seatId}</dt>
      <dd>
        {seat.status}
        {seat.objects !== null && ` · ${seat.objects} objects`}
        {seat.programRef && ` · ${seat.programRef}`}
        {seat.relationCheckRef && ` · relations ${seat.relationCheckRef}`}
      </dd>
    </>
  );
}

function ArtifactRow({
  artifact,
}: {
  artifact: CandidateDto["artifacts"][number];
}) {
  return (
    <>
      <dt>{artifact.fileName}</dt>
      <dd>
        {artifact.status ?? "no status"} · available {String(artifact.available)}
        {artifact.unavailableReason && ` · ${artifact.unavailableReason}`}
        {artifact.sha256 && ` · ${artifact.sha256}`}
        {artifact.objectCount !== null && ` · ${artifact.objectCount} objects`}
        {artifact.readbackVerified !== null &&
          ` · readback ${String(artifact.readbackVerified)}`}
      </dd>
    </>
  );
}
