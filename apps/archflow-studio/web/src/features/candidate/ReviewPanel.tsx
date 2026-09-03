/**
 * The human review step: the one button that starts a candidate run.
 *
 * A proposal whose status is `conflict` is not runnable, and the button says so
 * with the conflicting refs rather than merely greying out. Refusals the server
 * makes at request time — `PROPOSAL_NOT_RUNNABLE`, `STALE_BASE`,
 * `CANDIDATE_ID_COLLISION` — render here as the error they are.
 */

import { ErrorPanel } from "../../app/ErrorPanel";
import type { StudioApiError } from "../../api/client";
import type { ProposalDto } from "../../api/generated";

export function ReviewPanel({
  proposal,
  busy,
  error,
  onRun,
}: {
  proposal: ProposalDto | null;
  busy: boolean;
  error: StudioApiError | null;
  onRun(): void;
}) {
  if (proposal === null) {
    return (
      <p className="panel__note">
        no proposal yet. A candidate is a run of a typed proposal, never of a
        sentence.
      </p>
    );
  }
  const conflicted = proposal.status === "conflict";
  return (
    <div className="review">
      <button
        type="button"
        className="button button--primary"
        disabled={conflicted || busy}
        onClick={onRun}
      >
        {busy ? "queueing…" : "Run candidate"}
      </button>
      {conflicted && (
        <p className="panel__note panel__note--refused">
          this proposal reaches something the utterance asked to keep (
          <span className="mono">
            {proposal.impact.conflicts.join(", ") || "the server named none"}
          </span>
          ). The studio never runs a change past a protection the user named.
        </p>
      )}
      <p className="panel__note">
        a candidate is a harness run beside the project. It advances no stage and
        writes no canonical state.
      </p>
      {error && (
        <ErrorPanel
          error={error}
          what={`POST /api/proposals/${proposal.proposalId}/candidate`}
        />
      )}
    </div>
  );
}
