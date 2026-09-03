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

/**
 * What the picture on screen is, in three words the owner chose: CURRENT (the
 * loaded model as its receipt certifies it), GHOST PREVIEW (a proposal drawn
 * over it, approximate), VALIDATED (a candidate's export with its verdict
 * read). The detail is the server's word — never a colour alone.
 */
export interface ViewState {
  state: "current" | "ghost" | "validated";
  label: string;
  detail: string | null;
}

export function SourceChip({
  sourceLabel,
  inspection,
  status,
  message,
  view,
}: {
  sourceLabel: string | null;
  inspection: SceneInspection | null;
  status: ViewportStatus;
  message: string;
  view: ViewState | null;
}) {
  const { tag, rest } = tagOf(sourceLabel);
  return (
    <div className="source" data-tag={tag} data-state={view?.state ?? "none"}>
      {view && (
        <span className={`source__state source__state--${view.state}`}>
          {view.label}
          {view.detail && <span className="source__state-detail"> · {view.detail}</span>}
        </span>
      )}
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
