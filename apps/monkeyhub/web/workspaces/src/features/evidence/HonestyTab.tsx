import { useConnection } from "../../api/ProjectRuntimeContext";
/**
 * Every honesty line the server has said in this tab, and the identities it
 * said them about. Nothing is summarised; an empty list says so, because an
 * empty answer is a real one.
 *
 * The first line is which server said all of it. This is the quiet place for
 * it: an operator debugging "why does this say that" needs to know which
 * server and which mode answered, and everyone else never has to look.
 */

import { connectionLine, type ServerIdentity } from "../../api/connection";
import type {
  CandidateDto,
  StateProjectionDto,
  ValidationDto,
} from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import { useT } from "../../i18n/useT";

function Lines({ label, lines }: { label: string; lines: readonly string[] }) {
  const t = useT();
  const connection = useConnection();
  return (
    <div className="ev">
      <p className="label">{label}</p>
      {lines.length === 0 ? (
        <p className="ev__none">{t("evidence.honesty.none")}</p>
      ) : (
        <ul>
          {lines.map((line, index) => (
            <li key={`${index}:${line}`}>
              <BilingualText source={line} showSourceToggle />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function honestyCount(
  projection: StateProjectionDto | null,
  candidate: CandidateDto | null,
  validation: ValidationDto | null,
): number {
  return (
    (projection?.honesty.length ?? 0) +
    (candidate?.honesty.length ?? 0) +
    (validation?.honesty.length ?? 0)
  );
}

export function HonestyTab({
  server,
  projection,
  candidate,
  validation,
}: {
  server: ServerIdentity;
  projection: StateProjectionDto | null;
  candidate: CandidateDto | null;
  validation: ValidationDto | null;
}) {
  const t = useT();
  const connection = useConnection();
  const connected = (
    <div className="ev">
      <p className="label">{t("evidence.honesty.connection")}</p>
      <p className="verbatim-line">{connectionLine(server, connection.baseUrl)}</p>
      <p className="ev__none">
        {t("evidence.honesty.capabilities", { count: server.capabilities.length })} ·{" "}
        {server.capabilities.join(", ")}
      </p>
    </div>
  );
  if (projection === null) {
    return (
      <>
        {connected}
        <p className="ev__none">{t("evidence.honesty.noProjection")}</p>
      </>
    );
  }
  return (
    <>
      {connected}
      <Lines label={t("evidence.honesty.projectionLines")} lines={projection.honesty} />
      {candidate && (
        <Lines
          label={t("evidence.honesty.candidateLines", { id: candidate.candidateId })}
          lines={candidate.honesty}
        />
      )}
      {validation && (
        <Lines
          label={t("evidence.honesty.verdictLines", { id: validation.candidateId })}
          lines={validation.honesty}
        />
      )}
      <div className="ev">
        <p className="label">{t("evidence.honesty.identities")}</p>
        <dl>
          <dt>{t("evidence.fields.stateDigest")}</dt>
          <dd>{projection.stateDigest ?? t("evidence.honesty.stateDigestUnavailable")}</dd>
          <dt>{t("evidence.fields.recordDigest")}</dt>
          <dd>{projection.recordDigest}</dd>
          <dt>{t("evidence.honesty.published")}</dt>
          <dd>
            {t("evidence.honesty.issue", { version: projection.published.version })} ·{" "}
            {projection.published.stateSha256 ?? "—"}
          </dd>
          <dt>{t("settings.fields.referenceRun")}</dt>
          <dd>
            {projection.referenceRun.runId} · {projection.referenceRunSource} ·{" "}
            {t("evidence.honesty.baseVersion", {
              version: projection.referenceRun.baseVersion,
            })}
          </dd>
          <dt>{t("evidence.honesty.receiptMatch")}</dt>
          <dd>
            {projection.matchesReferenceReceipt === null
              ? t("evidence.honesty.notComparable")
              : String(projection.matchesReferenceReceipt)}
          </dd>
          <dt>{t("evidence.honesty.activePhase")}</dt>
          <dd>{projection.activePhase ?? "—"}</dd>
          <dt>{t("evidence.honesty.declared")}</dt>
          <dd>
            {t("evidence.honesty.componentCount", { count: projection.counts.components })} ·{" "}
            {t("evidence.honesty.parameterCount", { count: projection.counts.parameters })} ·{" "}
            {t("evidence.honesty.relationCount", { count: projection.counts.relations })} ·{" "}
            {t("evidence.honesty.edgeCount", { count: projection.counts.dependencyEdges })} ·{" "}
            {t("evidence.honesty.obligationCount", { count: projection.counts.obligations })}
          </dd>
        </dl>
      </div>
      {validation && (
        <div className="ev">
          <p className="label">{t("evidence.honesty.validationNote")}</p>
          <p className="verbatim-line">
            <BilingualText source={validation.validatorNote} showSourceToggle />
          </p>
          <p className="verbatim-line">
            <BilingualText source={validation.canonicalFacts} showSourceToggle />
          </p>
        </div>
      )}
    </>
  );
}
