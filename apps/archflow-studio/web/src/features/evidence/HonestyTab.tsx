/**
 * Every honesty line the server has said in this tab, and the identities it
 * said them about. Nothing is summarised; an empty list says so, because an
 * empty answer is a real one.
 *
 * The first line is which server said all of it. This is the quiet place for
 * it: an operator debugging "why does this say that" needs to know which
 * server and which mode answered, and everyone else never has to look.
 */

import { connection, connectionLine, type ServerIdentity } from "../../api/connection";
import type {
  CandidateDto,
  StateProjectionDto,
  ValidationDto,
} from "../../api/generated";

function Lines({ label, lines }: { label: string; lines: readonly string[] }) {
  return (
    <div className="ev">
      <p className="label">{label}</p>
      {lines.length === 0 ? (
        <p className="ev__none">none — the server had nothing to confess here</p>
      ) : (
        <ul>
          {lines.map((line, index) => (
            <li key={`${index}:${line}`}>{line}</li>
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
  const connected = (
    <div className="ev">
      <p className="label">Connection</p>
      <p className="verbatim-line">{connectionLine(server, connection.baseUrl)}</p>
      <p className="ev__none">
        {server.capabilities.length} capabilities ·{" "}
        {server.capabilities.join(", ")}
      </p>
    </div>
  );
  if (projection === null) {
    return (
      <>
        {connected}
        <p className="ev__none">no projection has been read in this tab</p>
      </>
    );
  }
  return (
    <>
      {connected}
      <Lines label="Record honesty · projection" lines={projection.honesty} />
      {candidate && (
        <Lines
          label={`Candidate honesty · ${candidate.candidateId}`}
          lines={candidate.honesty}
        />
      )}
      {validation && (
        <Lines
          label={`Verdict honesty · ${validation.candidateId}`}
          lines={validation.honesty}
        />
      )}
      <div className="ev">
        <p className="label">Identities</p>
        <dl>
          <dt>state digest</dt>
          <dd>{projection.stateDigest ?? "none — the kernel refused the bound view"}</dd>
          <dt>record digest</dt>
          <dd>{projection.recordDigest}</dd>
          <dt>published</dt>
          <dd>
            issue {projection.published.version} ·{" "}
            {projection.published.stateSha256 ?? "—"}
          </dd>
          <dt>reference run</dt>
          <dd>
            {projection.referenceRun.runId} · {projection.referenceRunSource} · base
            v{projection.referenceRun.baseVersion}
          </dd>
          <dt>receipt match</dt>
          <dd>
            {projection.matchesReferenceReceipt === null
              ? "not comparable"
              : String(projection.matchesReferenceReceipt)}
          </dd>
          <dt>active phase</dt>
          <dd>{projection.activePhase ?? "—"}</dd>
          <dt>declared</dt>
          <dd>
            {projection.counts.components} components · {projection.counts.parameters}{" "}
            parameters · {projection.counts.relations} relations ·{" "}
            {projection.counts.dependencyEdges} dependency edges ·{" "}
            {projection.counts.obligations} obligations
          </dd>
        </dl>
      </div>
      {validation && (
        <div className="ev">
          <p className="label">Validation note</p>
          <p className="verbatim-line">{validation.validatorNote}</p>
          <p className="verbatim-line">{validation.canonicalFacts}</p>
        </div>
      )}
    </>
  );
}
