/**
 * The selected candidate's records, as the server read them back: the run
 * receipt's seat rows, the exported artifacts with disk's answer, and the
 * kernel's validation receipt unedited.
 */

import type { CandidateDto, ValidationDto } from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import { useT } from "../../i18n/useT";

export function ReceiptsTab({
  candidate,
  validation,
  sentence,
}: {
  candidate: CandidateDto | null;
  validation: ValidationDto | null;
  sentence: string | null;
}) {
  const t = useT();
  if (candidate === null) {
    return <p className="ev__none">{t("evidence.receipts.noCandidate")}</p>;
  }
  return (
    <>
      <div className="ev">
        <p className="label">{t("evidence.receipts.candidateReceipt")}</p>
        {sentence && (
          <p className="ev__sentence">
            “{sentence}”
            <span className="quiet"> · {t("evidence.receipts.shownCandidate")}</span>
          </p>
        )}
        <dl>
          <dt>{t("evidence.fields.run")}</dt>
          <dd>{candidate.candidateId}</dd>
          <dt>{t("evidence.fields.job")}</dt>
          <dd>{candidate.jobId} · {candidate.status}</dd>
          <dt>{t("evidence.fields.proposal")}</dt>
          <dd>{candidate.proposalId}</dd>
          <dt>{t("evidence.fields.base")}</dt>
          <dd>
            v{candidate.base.version} · {candidate.base.stateSha256 ?? "—"}
          </dd>
          <dt>{t("evidence.fields.stateDigest")}</dt>
          <dd>{candidate.stateDigest ?? "—"}</dd>
          <dt>{t("evidence.fields.recordDigest")}</dt>
          <dd>{candidate.recordDigest ?? "—"}</dd>
          <dt>{t("evidence.fields.changed")}</dt>
          <dd>{String(candidate.changedVsProjection)}</dd>
          <dt>{t("evidence.fields.receipt")}</dt>
          <dd>{candidate.receiptRef}</dd>
          <dt>{t("evidence.fields.wallTime")}</dt>
          <dd>{candidate.wallTimeS === null ? "—" : `${candidate.wallTimeS} s`}</dd>
          <dt>{t("evidence.receipts.seatsComplete")}</dt>
          <dd>{String(candidate.seatExecutionComplete)}</dd>
          <dt>{t("evidence.receipts.harness")}</dt>
          <dd>{candidate.harness}</dd>
        </dl>
      </div>
      <div className="ev">
        <p className="label">{t("evidence.receipts.timings")}</p>
        <dl>
          <dt>{t("evidence.fields.run")}</dt>
          <dd>{candidate.timings.runS === null ? "—" : `${candidate.timings.runS} s`}</dd>
          {candidate.timings.seats.map((seat) => (
            <SeatTiming key={seat.seatId} seatId={seat.seatId} wallTimeS={seat.wallTimeS} />
          ))}
          {candidate.timings.exports.length === 0 ? (
            <>
              <dt>{t("evidence.receipts.exports")}</dt>
              <dd>{t("evidence.receipts.noExports")}</dd>
            </>
          ) : (
            candidate.timings.exports.map((item) => (
              <ExportTiming key={item.seatId} item={item} />
            ))
          )}
        </dl>
      </div>
      <div className="ev">
        <p className="label">
          {t("evidence.receipts.seatRows", { count: candidate.seatResults.length })}
        </p>
        {candidate.seatResults.length === 0 ? (
          <p className="ev__none">{t("evidence.receipts.noSeats")}</p>
        ) : (
          <dl>
            {candidate.seatResults.map((seat) => (
              <SeatRow key={seat.seatId} seat={seat} />
            ))}
          </dl>
        )}
      </div>
      <div className="ev">
        <p className="label">
          {t("evidence.receipts.exportedArtifacts", { count: candidate.artifacts.length })}
        </p>
        {candidate.artifacts.length === 0 ? (
          <p className="ev__none">{t("evidence.receipts.noModelExported")}</p>
        ) : (
          <dl>
            {candidate.artifacts.map((artifact) => (
              <ArtifactRow key={artifact.artifactId} artifact={artifact} />
            ))}
          </dl>
        )}
        {candidate.skippedRuns.length > 0 && (
          <p className="verbatim-line">
            {t("evidence.receipts.skippedRuns")}: {candidate.skippedRuns.join(", ")}
          </p>
        )}
      </div>
      <div className="ev">
        <p className="label">{t("evidence.receipts.validationReceipt")}</p>
        {validation === null ? (
          <p className="ev__none">{t("evidence.receipts.noVerdict")}</p>
        ) : (
          <>
            <dl>
              <dt>{t("evidence.fields.receipt")}</dt>
              <dd>{validation.receipt.receiptId}</dd>
              <dt>{t("evidence.receipts.submission")}</dt>
              <dd>
                {validation.receipt.submissionId} · {validation.receipt.submissionDigest}
              </dd>
              <dt>{t("evidence.receipts.checkedState")}</dt>
              <dd>
                v{validation.receipt.checkedState.version} ·{" "}
                {validation.receipt.checkedState.stateSha256 ?? "—"}
              </dd>
              <dt>{t("evidence.receipts.passed")}</dt>
              <dd>{String(validation.receipt.passed)}</dd>
              <dt>{t("evidence.receipts.validators")}</dt>
              <dd>{validation.validators.join(" · ")}</dd>
              <dt>{t("evidence.receipts.effective")}</dt>
              <dd>
                {validation.effectiveChecks.length === 0
                  ? t("evidence.common.none")
                  : validation.effectiveChecks.join(" · ")}
              </dd>
              <dt>{t("evidence.receipts.advance")}</dt>
              <dd>{String(validation.advance)}</dd>
              <dt>{t("evidence.receipts.blockedBy")}</dt>
              <dd>
                {validation.blockedBy.length === 0
                  ? t("evidence.receipts.noClauseRefused")
                  : validation.blockedBy.join(" · ")}
              </dd>
            </dl>
            <p className="label">
              {t("evidence.receipts.findings", {
                count: validation.receipt.findings.length,
              })}
            </p>
            {validation.receipt.findings.length === 0 ? (
              <p className="ev__none">{t("evidence.receipts.noFindings")}</p>
            ) : (
              <ul>
                {validation.receipt.findings.map((finding, index) => (
                  <li key={`${finding.code}:${index}`}>
                    <span className="mono">{finding.code}</span> · {finding.severity}{" "}
                    · <BilingualText source={finding.message} showSourceToggle />
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

function SeatTiming({ seatId, wallTimeS }: { seatId: string; wallTimeS: number | null }) {
  const t = useT();
  return (
    <>
      <dt>{seatId}</dt>
      <dd>{wallTimeS === null ? t("evidence.receipts.noWallTime") : `${wallTimeS} s`}</dd>
    </>
  );
}

function ExportTiming({ item }: { item: CandidateDto["timings"]["exports"][number] }) {
  const t = useT();
  return (
    <>
      <dt>{t("evidence.receipts.export")} · {item.seatId}</dt>
      <dd>
        {item.path ?? t("evidence.receipts.pathUnknown")} ·{" "}
        {item.seconds === null ? t("evidence.receipts.noSeconds") : `${item.seconds} s`}{" "}
        · {item.status ?? t("evidence.receipts.noStatus")}
        {item.rebuildRatio !== null &&
          ` · ${t("evidence.receipts.rebuiltRatio", {
            rebuilt: item.rebuiltObjects ?? 0,
            total: (item.rebuiltObjects ?? 0) + (item.keptObjects ?? 0),
            ratio: item.rebuildRatio.toFixed(3),
          })}`}
      </dd>
    </>
  );
}

function SeatRow({ seat }: { seat: CandidateDto["seatResults"][number] }) {
  const t = useT();
  return (
    <>
      <dt>{seat.seatId}</dt>
      <dd>
        {seat.status}
        {seat.objects !== null && ` · ${t("evidence.receipts.objectCount", { count: seat.objects })}`}
        {seat.programRef && ` · ${seat.programRef}`}
        {seat.relationCheckRef && ` · ${t("evidence.receipts.relations")} ${seat.relationCheckRef}`}
      </dd>
    </>
  );
}

function ArtifactRow({
  artifact,
}: {
  artifact: CandidateDto["artifacts"][number];
}) {
  const t = useT();
  return (
    <>
      <dt>{artifact.fileName}</dt>
      <dd>
        {artifact.status ?? t("evidence.receipts.noStatus")} ·{" "}
        {t("evidence.receipts.available")} {String(artifact.available)}
        {artifact.unavailableReason && (
          <> · <BilingualText source={artifact.unavailableReason} showSourceToggle /></>
        )}
        {artifact.sha256 && ` · ${artifact.sha256}`}
        {artifact.objectCount !== null &&
          ` · ${t("evidence.receipts.objectCount", { count: artifact.objectCount })}`}
        {artifact.readbackVerified !== null &&
          ` · ${t("evidence.receipts.readback")} ${String(artifact.readbackVerified)}`}
      </dd>
    </>
  );
}
