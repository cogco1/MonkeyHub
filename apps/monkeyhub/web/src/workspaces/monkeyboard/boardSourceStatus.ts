/**
 * #288: what the selected Board page says about its source. A page linked to a
 * model is judged against the editing base (the Working Head) by the Runtime's
 * one representation status (#223), the answer the Drawing tool, the Worktree
 * Graph, Render and Publish also read; the Board never compares sources itself.
 * The card names the page's own Stage and generation time, not its raw source.
 */
import type { DesignHistoryDto, RepresentationStatusDto, SourceDocumentDto } from "../../api/project-runtime/generated";

export type PageSourceState = "unlinked" | "checking" | RepresentationStatusDto["state"];

/** One exact page's status as read; `status` is null when the read failed. */
export interface PageStatusRead {
  readonly key: string;
  readonly status: RepresentationStatusDto | null;
}

/**
 * The index entities a page's status is derived from, and so the only ones whose move reads
 * it again: the runs (documents, receipts, render requests), the design branches and the
 * working position the head comes from, and HEAD for a project without one. Not what a run
 * keeps aside (`aside:<id>`: each Board save and page ink), nor a thumbnail.
 */
export function pageStatusShows(id: string): boolean {
  return id === "tree" || id === "working" || id === "area:head" || id.startsWith("run:");
}

/** A page with no model says so; otherwise the Runtime's answer for exactly this page, once it has one. */
export function pageSourceState(document: SourceDocumentDto, key: string, boardProjectId: string,
  read: PageStatusRead | null): PageSourceState {
  if (!document.modelSource) return "unlinked";
  // A page naming another project cannot be judged against this project's editing base.
  if (document.projectId !== boardProjectId) return "unavailable";
  if (read === null || read.key !== key) return "checking";
  if (read.status === null || read.status.projectId !== boardProjectId) return "unavailable";
  return read.status.state;
}

/** The Runtime's own detail for the shown state, kept for a tooltip; null while there is none. */
export function pageSourceReason(state: PageSourceState, read: PageStatusRead | null): string | null {
  return state === "unlinked" || state === "checking" ? null : read?.status?.reason ?? null;
}

/**
 * The project's Stage labels by ref. The page's Stage is looked for on the main line first
 * and then on each other branch; a history of another project names nothing here.
 */
export async function readStageLabels(history: (branchId?: string) => Promise<DesignHistoryDto>, projectId: string,
  stageRef: string): Promise<Map<string, string>> {
  const labels = new Map<string, string>();
  const keep = (read: DesignHistoryDto) => {
    if (read.projectId !== projectId) return;
    for (const stage of read.stages) labels.set(stage.stageRef, stage.label);
  };
  const main = await history();
  keep(main);
  if (main.projectId !== projectId || labels.has(stageRef)) return labels;
  for (const branch of main.branches) {
    if (branch.branchId === main.branchId) continue;
    keep(await history(branch.branchId));
    if (labels.has(stageRef)) break;
  }
  return labels;
}

/** The page's Stage, as its project names it: undefined before it was looked up, null when no Stage names it. */
export function pageStageLabel(document: SourceDocumentDto, labels: ReadonlyMap<string, string | null>): string | null | undefined {
  return document.sourceStageRef ? labels.get(document.sourceStageRef) : undefined;
}

const detailCopy = {
  en: { stage: "Stage", unknownStage: "Unknown stage", generated: "Generated" },
  "zh-CN": { stage: "阶段", unknownStage: "未知阶段", generated: "生成于" },
};

/** "Stage · <label> · Generated <date>", each part only where the page has it. */
export function pageSourceDetails(document: SourceDocumentDto, stageLabel: string | null | undefined,
  language: "en" | "zh-CN"): string {
  const text = detailCopy[language];
  const parts: string[] = [];
  if (document.sourceStageRef && stageLabel !== undefined) parts.push(`${text.stage} · ${stageLabel ?? text.unknownStage}`);
  if (document.generatedAt) {
    const generated = new Date(document.generatedAt);
    if (!Number.isNaN(generated.getTime())) {
      parts.push(`${text.generated} ${generated.toLocaleString(language, { dateStyle: "medium", timeStyle: "short" })}`);
    }
  }
  return parts.join(" · ");
}
