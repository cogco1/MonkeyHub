/**
 * A proposed change, in design-review language.
 *
 * Change is the record's number and the proposed one; Will update is the
 * kernel's closure, listed not re-derived; Keep is what the sentence protected.
 * When an agent compiled the sentence, its reading is printed above the card as
 * the agent's — provider, model, its own "why" — so the reader can tell the
 * agent's words from the record's answer. Apply runs the candidate; Adjust puts
 * the compiled sentence back in the composer.
 */

import type { AgentReadingDto, ProposalDto } from "../../../api/generated";
import type { EvidenceTab } from "../../../app/evidence";
import { Verbatim } from "./Verbatim";

const HARNESS_SENTENCE =
  "a harness run beside the project; no stage advances, nothing is written to HEAD";

/** A kernel ref without its prefix, for the eye; the ref itself is in the title attribute. */
function shortRef(ref: string): string {
  const colon = ref.indexOf(":");
  return colon === -1 ? ref : ref.slice(colon + 1);
}

export function ProposalCard({
  proposal,
  agent,
  busy,
  onRun,
  onAdjust,
  onEvidence,
}: {
  proposal: ProposalDto;
  agent: AgentReadingDto | null;
  busy: boolean;
  onRun(): void;
  onAdjust(utterance: string): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const { target, change, impact } = proposal;
  const subject = target.elementId ?? target.componentId;
  const compiled = agent?.compiledUtterance ?? proposal.utterance;
  const readByAgent = agent !== null && agent.provider !== "deterministic";
  return (
    <article className="card card--proposal">
      {readByAgent && (
        <div className="card__row card__agent">
          <p className="label">
            Read by {agent.provider}
            {agent.model ? ` · ${agent.model}` : ""} ·{" "}
            {(agent.latencyMs / 1000).toFixed(1)} s
          </p>
          {agent.why && <p className="verbatim-line">{agent.why}</p>}
          <p className="quiet">
            compiled to <code>{agent.compiledUtterance}</code>
          </p>
        </div>
      )}
      <div className="card__row">
        <p className="label">Proposed change</p>
        <p className="card__title">
          <span className="mono">{subject}</span>
          {target.elementId && (
            <span className="quiet"> · {target.componentId}</span>
          )}
        </p>
      </div>
      <div className="card__row">
        <dl className="kv kv--review">
          <dt>Change</dt>
          <dd className="delta">
            {target.key} {String(change.old)} → <b>{String(change.new)}</b>
            {change.unit ? ` ${change.unit}` : ""}
            {change.unit === null && (
              <span className="quiet"> (the record's own units)</span>
            )}
          </dd>
          <dt>Will update</dt>
          <dd>
            {impact.propagated.length === 0 ? (
              <span className="quiet">nothing downstream is declared</span>
            ) : (
              <ul className="reflist">
                {impact.propagated.map((ref) => (
                  <li key={ref} className="mono" title={ref}>
                    {shortRef(ref)}
                  </li>
                ))}
              </ul>
            )}
            {impact.unknownCoverage.count > 0 && (
              <span className="quiet">
                {" "}
                · {impact.unknownCoverage.count} components with unknown
                coverage{" "}
                <button
                  type="button"
                  className="btn btn--link"
                  onClick={() => onEvidence("honesty")}
                >
                  why
                </button>
              </span>
            )}
          </dd>
          <dt>Keep</dt>
          <dd>
            {proposal.protected.length === 0 ? (
              <span className="quiet">nothing was named</span>
            ) : (
              <ul className="reflist">
                {proposal.protected.map((ref) => (
                  <li key={ref} className="mono" title={ref}>
                    {shortRef(ref)}
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </dl>
        <Verbatim lines={impact.honesty} />
      </div>
      {proposal.status === "conflict" && (
        <div className="card__row">
          <p className="card__conflict">
            Conflict: the change reaches what you asked to keep —{" "}
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
          Apply
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => onAdjust(compiled)}
        >
          Adjust
        </button>
        <span className="quiet">{HARNESS_SENTENCE}</span>
      </div>
    </article>
  );
}
