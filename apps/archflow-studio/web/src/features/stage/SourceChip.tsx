/**
 * Which file the viewport is showing, in the shell's own words: the label the
 * viewer reported when it accepted the bytes, split into a tag and the rest,
 * with the loader's own counts beside it.
 */

import type { SceneInspection } from "../../viewer/sceneInspection";
import {
  LOCAL_SOURCE_LABEL,
  type ViewportStatus,
} from "../../viewer/ThreeDmViewport";

function tagOf(sourceLabel: string | null): { tag: string; rest: string } {
  if (sourceLabel === null) return { tag: "NO MODEL", rest: "" };
  if (sourceLabel === LOCAL_SOURCE_LABEL) return { tag: "LOCAL", rest: "unbound" };
  const [head, ...rest] = sourceLabel.split(" · ");
  return { tag: head === "CANONICAL" ? "RUN" : head, rest: rest.join(" · ") };
}

export function SourceChip({
  sourceLabel,
  inspection,
  status,
  message,
}: {
  sourceLabel: string | null;
  inspection: SceneInspection | null;
  status: ViewportStatus;
  message: string;
}) {
  const { tag, rest } = tagOf(sourceLabel);
  return (
    <div className="source" data-tag={tag}>
      <span className="source__tag">{tag}</span>
      {rest && <span className="mono">{rest}</span>}
      {inspection && (
        <span className="mono source__facts">
          {inspection.fileName} · {inspection.meshCount.toLocaleString()} meshes ·{" "}
          {inspection.objectCount.toLocaleString()} objects
        </span>
      )}
      {status !== "ready" && message && (
        <span className="source__status">{message}</span>
      )}
    </div>
  );
}
