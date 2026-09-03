/**
 * The typed proposal the server made of the sentence.
 *
 * The operator is the kernel's `DecisionOperator@2` payload and it travels as
 * an opaque object: it is displayed as JSON behind a toggle and is never edited
 * here. `persistence` is printed verbatim because it is the line that says this
 * proposal is not version history.
 */

import { useState } from "react";

import type { ProposalDto } from "../../api/generated";

export function ProposalPanel({ proposal }: { proposal: ProposalDto }) {
  const [showOperator, setShowOperator] = useState(false);
  const operator = proposal.decisionOperator as Record<string, unknown>;
  const decisionType = operator["decision_type"];
  const baseDigest = operator["base_state_digest"];

  return (
    <div className="proposal">
      <div className="chips">
        <span
          className={`chip ${
            proposal.status === "conflict" ? "chip--violated" : "chip--held"
          }`}
        >
          {proposal.status}
        </span>
        <span className="chip chip--neutral mono">{proposal.proposalId}</span>
      </div>
      <dl className="facts">
        <dt>utterance</dt>
        <dd>{proposal.utterance}</dd>
        <dt>target</dt>
        <dd className="mono">
          {proposal.target.componentId}
          {proposal.target.elementId ? ` · ${proposal.target.elementId}` : ""}
        </dd>
        <dt>ref · key</dt>
        <dd className="mono">
          {proposal.target.ref} · {proposal.target.key}
        </dd>
        <dt>change</dt>
        <dd className="mono">
          {proposal.change.old} → {proposal.change.new}
          {proposal.change.unit ? ` ${proposal.change.unit}` : ""}
        </dd>
        <dt>protected</dt>
        <dd className="mono">
          {proposal.protected.length === 0
            ? "nothing was named"
            : proposal.protected.join(", ")}
        </dd>
        <dt>base state</dt>
        <dd className="mono">{proposal.baseStateDigest}</dd>
        <dt>record</dt>
        <dd className="mono">{proposal.recordDigest}</dd>
        <dt>created</dt>
        <dd className="mono">{proposal.createdAt}</dd>
      </dl>
      <p className="panel__note">{proposal.persistence}</p>
      <button
        type="button"
        className="button"
        onClick={() => setShowOperator((shown) => !shown)}
      >
        {showOperator ? "hide typed operator" : "show typed operator"}
      </button>
      {showOperator && (
        <>
          <p className="panel__note">
            decision_type <span className="mono">{String(decisionType)}</span> ·
            base_state_digest <span className="mono">{String(baseDigest)}</span>
          </p>
          <pre className="json">{JSON.stringify(operator, null, 2)}</pre>
        </>
      )}
    </div>
  );
}
