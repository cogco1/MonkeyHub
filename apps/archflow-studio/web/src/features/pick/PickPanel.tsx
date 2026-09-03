/**
 * What the server said the last clicked object was.
 *
 * Three answers are possible and they stay apart here: *resolved* names a
 * component, *unbound* says the object carries no archflow identity, and
 * *unknown_component* says it claims one this record does not declare. The
 * client renders whichever came back and decides none of them.
 *
 * `sourceState` is about the file, not the object: `unknown` is not `current`.
 */

import type { PickResolutionDto } from "../../api/generated";

const SOURCE_CHIP: Record<string, string> = {
  current: "chip--held",
  stale: "chip--unchecked",
  unknown: "chip--neutral",
};

export function PickPanel({ pick }: { pick: PickResolutionDto }) {
  return (
    <div className="pick">
      <div className="chips">
        <span
          className={`chip ${
            pick.status === "resolved" ? "chip--held" : "chip--unchecked"
          }`}
        >
          {pick.status}
        </span>
        <span className={`chip ${SOURCE_CHIP[pick.sourceState] ?? "chip--neutral"}`}>
          source {pick.sourceState}
        </span>
      </div>
      <dl className="facts">
        <dt>component</dt>
        <dd className="mono">{pick.componentId ?? "—"}</dd>
        <dt>element</dt>
        <dd className="mono">{pick.elementId ?? "—"}</dd>
        <dt>operation</dt>
        <dd className="mono">{pick.operationId ?? "—"}</dd>
        <dt>source run</dt>
        <dd className="mono">{pick.sourceRun ?? "—"}</dd>
        <dt>source program</dt>
        <dd className="mono">{pick.sourceProgramDigest ?? "—"}</dd>
      </dl>
      {pick.detail && <p className="panel__note">{pick.detail}</p>}
    </div>
  );
}
