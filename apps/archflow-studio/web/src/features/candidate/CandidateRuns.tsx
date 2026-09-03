/**
 * The candidate runs this tab launched for the current proposal.
 *
 * A proposal may have many candidate runs and the server marks none of them
 * "latest" — so this list is the client's own record of what *it* started,
 * newest first, and selecting one is how you look at an earlier run. It is not
 * a history of the project and it does not survive a reload; the runs
 * themselves live in the project's run records, which the server answers for.
 */

export interface LaunchedCandidate {
  readonly candidateId: string;
  readonly jobId: string;
  readonly proposalId: string;
  readonly startedAt: string;
  readonly status: string;
}

export function CandidateRuns({
  launched,
  selectedCandidateId,
  onSelect,
}: {
  launched: readonly LaunchedCandidate[];
  selectedCandidateId: string | null;
  onSelect(candidateId: string): void;
}) {
  if (launched.length === 0) {
    return (
      <p className="panel__note">
        this tab has launched no candidate runs. The server marks no "latest"
        candidate, so nothing is shown until one is started here.
      </p>
    );
  }
  return (
    <ul className="rows">
      {launched.map((run) => (
        <li key={run.candidateId}>
          <button
            type="button"
            className={`row${
              run.candidateId === selectedCandidateId ? " is-selected" : ""
            }`}
            onClick={() => onSelect(run.candidateId)}
          >
            <span className="row__id mono">{run.candidateId}</span>
            <span className="row__meta">{run.status}</span>
            <span className="row__meta mono">{run.proposalId}</span>
            <span className="row__meta mono">{run.startedAt}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
