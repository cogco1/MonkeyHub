/**
 * How the tree's facts read in the viewer's language. One place, so the
 * chip, the canvas, the list and the side card name a node the same way,
 * and no raw id or hash reaches the main copy.
 */
import type { TFunction } from "../../i18n/useT";
import type { Fork } from "./layout";
import { CURRENT, type GrowthTree, type PendingStatus, type TreeNode } from "./model";
import type { SceneWords } from "./scene";

/** The actor id an explicit act in the Studio carries when nobody signed in. */
const LOCAL_ACTOR = "studio:explicit-user-action";

export type SurfaceName = "arch" | "board" | "drawing" | "render" | "publish";

export function treeWords(t: TFunction, tree: GrowthTree | null) {
  const stageName = (node: TreeNode) => `S${node.stage!.number}${node.stage!.name ? ` · ${node.stage!.name}` : ""}`;
  const optionName = (node: TreeNode) => node.label
    ? `${node.letter ? `${node.letter} · ` : ""}${node.label}`
    : t("designTree.optionUnnamed", { letter: node.letter ?? "" }).trim();
  const pendingText = (status: PendingStatus) => t(status === "running" ? "designTree.pending.running"
    : status === "queued" ? "designTree.pending.queued" : "designTree.pending.interrupted");
  const title = (node: TreeNode): string => node.kind === "stage" ? stageName(node)
    : node.kind === "candidate" ? optionName(node)
      : node.kind === "pending" ? node.label ?? t("designTree.pending.unnamed")
        : node.kind === "current" ? t("designTree.current") : t("designTree.origin");
  const byId = (id: string | null) => (id && tree?.nodes.get(id)) || null;
  // A Study a closed loop admitted has no name of its own: it is named after where it started.
  const studyName = (id: string | null): string | null => {
    const study = id ? tree?.studies.get(id) : undefined;
    if (!study) return null;
    if (study.label) return study.label;
    const base = byId(byId(study.members[0] ?? null)?.parent ?? null);
    return base ? t("designTree.study.from", { base: title(base) }) : t("designTree.study.unnamed");
  };
  const actor = (value: string | null) => !value ? null : value === LOCAL_ACTOR ? t("designTree.actor.you") : value;
  const currentAt = (): string => {
    const current = byId(CURRENT);
    const anchor = byId(current?.parent ?? null);
    if (!current?.current || !anchor) return "";
    const name = title(anchor);
    const edits = current.current.editsAfter;
    return edits === 0 ? t("designTree.currentAt", { node: name })
      : t(edits === 1 ? "designTree.currentAfterOne" : "designTree.currentAfter", { node: name, count: edits });
  };
  const status = (node: TreeNode): string => {
    if (!tree) return "";
    if (node.kind === "pending") return pendingText(node.pending!.status);
    if (node.kind !== "candidate") return "";
    const accepted = byId(node.candidate!.acceptedStage);
    if (tree.onTrunk.has(node.id)) {
      return byId(CURRENT)?.parent === node.id ? t("designTree.status.current") : accepted
        ? t("designTree.status.accepted", { stage: stageName(accepted) }) : t("designTree.status.line");
    }
    if (tree.continued.has(node.id)) return accepted ? t("designTree.status.acceptedEarlier", { stage: stageName(accepted) }) : t("designTree.status.earlier");
    return t("designTree.status.ready");
  };
  // Longest first; the far view shortens a count rather than letting it collide.
  const fork = (value: Fork): string[] => {
    const options = value.options ? t(value.options === 1 ? "designTree.fork.option" : "designTree.fork.options", { count: value.options }) : "";
    const continued = value.continued ? t("designTree.fork.continued", { count: value.continued }) : "";
    const running = value.pending ? t("designTree.fork.running", { count: value.pending }) : "";
    const variants = [[options, continued, running], [options, running], [options || running]]
      .map((parts) => parts.filter(Boolean).join(" · ")).concat(String(value.options + value.pending));
    return [...new Set(variants.filter(Boolean))];
  };
  const accept = tree?.accept;
  const scene: SceneWords = {
    current: t("designTree.current"), origin: t("designTree.origin"), stage: stageName, option: (node) => node.kind === "pending"
      ? node.label ?? t("designTree.pending.unnamed") : optionName(node),
    pending: pendingText, currentAt: currentAt(),
    accept: accept?.nextLabel ? t("designTree.action.accept", { stage: accept.nextLabel }) : t("designTree.action.acceptNext"),
    acceptBlocked: t("designTree.action.acceptNext"), status, fork,
  };
  return { stageName, optionName, studyName, title, actor, currentAt, status, fork, pendingText, scene, byId };
}

export type TreeWords = ReturnType<typeof treeWords>;

/** "3 Sep · 14:26", in the viewer's language; the raw timestamp stays in the details. */
export function whenText(value: string | null, language: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
