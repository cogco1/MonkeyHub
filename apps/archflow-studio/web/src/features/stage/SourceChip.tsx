/**
 * Which file the viewport is showing, in the shell's own words: the label the
 * viewer reported when it accepted the bytes, split into a tag and the rest,
 * with the loader's own counts beside it.
 */

import type { SceneInspection } from "../../viewer/sceneInspection";
import { useT } from "../../i18n/useT";
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
 * over it, approximate), REVIEW-READY (a candidate export whose readiness was
 * read). The detail is the server's word — never a colour alone.
 */
export interface ViewState {
  state: "current" | "ghost" | "validated" | "blocked";
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
  const t = useT();
  const { tag, rest } = tagOf(sourceLabel);
  const displayedTag =
    tag === "NO MODEL"
      ? t("stage.source.noModel")
      : tag === "LOCAL"
        ? t("stage.source.local")
        : tag === "RUN"
          ? t("stage.source.run")
          : tag;
  const displayedRest = tag === "LOCAL" && rest === "unbound"
    ? t("stage.source.unbound")
    : rest;
  const viewLabel = view
    ? ({
        "Ghost preview": t("stage.view.ghost"),
        "Review-ready": t("stage.view.validated"),
        Checked: t("stage.view.checked"),
        "Candidate export": t("stage.view.candidateExport"),
        Current: t("stage.view.current"),
      } as Readonly<Record<string, string>>)[view.label] ?? view.label
    : null;
  const viewDetail = (() => {
    if (!view?.detail) return null;
    if (view.detail === "approximate") return t("stage.view.approximate");
    if (view.detail === "ready for review") return t("stage.view.reviewReady");
    if (view.detail === "verdict not read yet") return t("stage.view.verdictUnread");
    if (view.detail.startsWith("blocked: ")) {
      return (
        <>
          {t("stage.view.blocked")}: {view.detail.slice("blocked: ".length)}
        </>
      );
    }
    return <span lang="en" translate="no">{view.detail}</span>;
  })();
  return (
    <div className="source" data-tag={tag} data-state={view?.state ?? "none"}>
      {view && (
        <span className={`source__state source__state--${view.state}`}>
          {viewLabel}
          {viewDetail && <span className="source__state-detail"> · {viewDetail}</span>}
        </span>
      )}
      <span className="source__tag">{displayedTag}</span>
      {displayedRest && <span className="mono">{displayedRest}</span>}
      {inspection && (
        <span className="mono source__facts">
          {inspection.fileName} ·{" "}
          {t("stage.source.meshes", {
            count: inspection.meshCount.toLocaleString(),
          })}{" "}
          ·{" "}
          {t("stage.source.objects", {
            count: inspection.objectCount.toLocaleString(),
          })}
        </span>
      )}
      {status !== "ready" && message && (
        <span className="source__status">
          {message ===
          "No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here"
            ? t("stage.source.status.noModel")
            : <span lang="en" translate="no">{message}</span>}
        </span>
      )}
    </div>
  );
}
