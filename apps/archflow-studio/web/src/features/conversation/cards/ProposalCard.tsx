/**
 * A typed proposal, as the server minted it.
 *
 * The change is the record's number and the proposed one; the impact is the
 * kernel's closure counted, never re-derived; and the honesty lines that gate
 * the impact sentence are quoted right under it, because a sentence whose
 * caveat lives one click away is a sentence that reads stronger than it is.
 * The Run button is on this card: a candidate belongs to a proposal.
 */

import type { ProposalDto } from "../../../api/generated";
import type { EvidenceTab } from "../../../app/evidence";
import { Verbatim } from "./Verbatim";

const HARNESS_SENTENCE =
  "a harness run beside the project; no stage advances, nothing is written to HEAD";

export function ProposalCard({
  proposal,
  busy,
  onRun,
  onEvidence,
}: {
  proposal: ProposalDto;
  busy: boolean;
  onRun(): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const { target, change, impact } = proposal;
  const subject = target.elementId ?? target.componentId;
  return (
    <article className="card">
      <div className="card__row">
        <p className="card__title">
          Set <span className="mono">{target.key}</span> on{" "}
          <span className="mono">{subject}</span>
        </p>
        <p className="delta">
          {String(change.old)} → <b>{String(change.new)}</b>{" "}
          <span className="quiet">
            {change.unit ?? "in the record's own units"}
          </span>
        </p>
      </div>
      <div className="card__row">
        <dl className="kv">
          <dt>component</dt>
          <dd>
            <span className="mono">{target.componentId}</span>
            {target.elementId && (
              <>
                {" · element "}
                <span className="mono">{target.elementId}</span>
              </>
            )}
            {" · "}
            <span className="mono">{target.ref}</span>
          </dd>
          <dt>protected</dt>
          <dd>
            {proposal.protected.length === 0 ? (
              "nothing was named"
            ) : (
              <span className="mono">{proposal.protected.join(", ")}</span>
            )}
          </dd>
          <dt>impact</dt>
          <dd>
            {impact.direct.length} direct · {impact.propagated.length}{" "}
            propagated · {impact.conflicts.length} conflicts ·{" "}
            {impact.unknownCoverage.count} components with unknown coverage
            {impact.locks.length > 0 && ` · ${impact.locks.length} locks`}
            {impact.honesty.length > 0 && (
              <>
                {" "}
                <button
                  type="button"
                  className="btn btn--link"
                  onClick={() => onEvidence("honesty")}
                >
                  why
                </button>
              </>
            )}
          </dd>
        </dl>
        <Verbatim lines={impact.honesty} />
      </div>
      {proposal.status === "conflict" && (
        <div className="card__row">
          <p className="card__conflict">
            Conflict: the change reaches what the utterance asked to keep —{" "}
            <span className="mono">{impact.conflicts.join(", ")}</span>
          </p>
        </div>
      )}
      <div className="card__row actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy}
          onClick={onRun}
        >
          Run candidate
        </button>
        <span className="quiet">{HARNESS_SENTENCE}</span>
      </div>
    </article>
  );
}
