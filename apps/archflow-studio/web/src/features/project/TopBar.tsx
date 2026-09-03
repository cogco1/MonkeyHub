/**
 * What this tab is looking at, stated once at the top.
 *
 * Everything here is a value the server sent. The authority chip is a fixed
 * label rather than a flag off the wire: the API carries no constant-false
 * authority fields, and round-1 is proposal-only by construction — so the shell
 * states the standing fact instead of pretending to read it.
 */

import type { ProjectBindingDto, StateProjectionDto } from "../../api/generated";
import { sha8 } from "../../app/format";

const AUTHORITY = "PROPOSAL-ONLY · CANONICAL WRITE DISABLED";

function receiptChip(matches: boolean | null | undefined) {
  if (matches === null || matches === undefined) {
    return <span className="chip chip--neutral">NO RECEIPT</span>;
  }
  return matches ? (
    <span className="chip chip--held">RECEIPT MATCH</span>
  ) : (
    <span className="chip chip--violated">RECEIPT MISMATCH</span>
  );
}

export function TopBar({
  project,
  projection,
}: {
  project: ProjectBindingDto;
  projection: StateProjectionDto;
}) {
  const receipt = projection.referenceReceipt;
  return (
    <div className="topbar">
      <span className="topbar__project">{project.projectId}</span>
      <span className="topbar__item">
        HEAD v{project.head.version} · {sha8(project.head.stateSha256)}
      </span>
      <span className="topbar__item">
        reference run {projection.referenceRun.runId} ·{" "}
        {projection.referenceRunSource}
      </span>
      <span className="topbar__item">
        base v{projection.referenceRun.baseVersion} ·{" "}
        {sha8(projection.referenceRun.baseSha256)}
      </span>
      <span className="topbar__item">phase {projection.activePhase}</span>
      <span className="topbar__item">
        {projection.stageBinding.stageId
          ? `stage ${projection.stageBinding.stageId}`
          : "NO STAGE BINDING"}
      </span>
      <span className="topbar__item">
        state {sha8(projection.stateDigest)} · record{" "}
        {sha8(projection.recordDigest)}
      </span>
      <span className="topbar__item">
        {receipt
          ? `receipt ${receipt.runId} · ${sha8(receipt.designStateDigest)}`
          : "NO RECEIPT"}
      </span>
      {receiptChip(projection.matchesReferenceReceipt)}
      <span className="chip chip--authority">{AUTHORITY}</span>
    </div>
  );
}
