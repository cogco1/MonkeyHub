/**
 * One project's Design Tree: its retained facts, read together, and the
 * actions that change them through the routes that already own them.
 *
 * Continue is the existing working-position write (`PUT /api/working-draft`,
 * the path `changeEditingBase` takes); its Undo is the same write onto the
 * Current it replaced (continueUndo.ts). Accept as next Stage is the existing
 * `POST /api/candidates/{id}/accept`, on the Working Head only. View changes
 * nothing here. Each act that changed the design confirms itself in a toast
 * beside the chip (FN-5); a refusal stays inline where it was asked for. The
 * facts are read again on a short interval while the workspace is on screen,
 * on focus, and after each action.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { asStudioApiError, StudioApiError, type StudioClient } from "../../api/client";
import type { DesignHistoryDto, WorkingDraftDto, WorkingDraftSelectionDto } from "../../api/generated";
import type { DesignTreeSource } from "./contract";
import { continueRequest, continueUndo, DESIGN_TREE_UNDO_MOVED, DESIGN_TREE_UNSYNCED, undoRequest, type ContinueUndo,
  type HeadTarget } from "./continueUndo";
import { buildGrowthTree, type GrowthTree, type TreeNode } from "./model";

export { DESIGN_TREE_UNDO_MOVED, DESIGN_TREE_UNSYNCED } from "./continueUndo";

/** How often an open project re-reads its tree while it is on screen. */
export const DESIGN_TREE_POLL_MS = 10_000;

export type DesignTreeAction = { readonly kind: "continue" | "review"; readonly node: string } | { readonly kind: "accept" } | { readonly kind: "undo" };

export interface DesignTreeOutcome {
  readonly kind: "continued" | "accepted" | "refused";
  readonly node?: string;
  readonly stageLabel?: string;
  readonly error?: StudioApiError;
}

/**
 * The confirmation beside the chip after an act that changed the design (FN-5).
 * Continue names the new Current, with Undo when the Current it replaced can be
 * put back exactly; a refused Undo says why in the same toast. Accept names the
 * new Stage and has no Undo: acceptance is a retained fact. Every toast gets a
 * fresh id, so its time on screen starts again.
 */
export type DesignTreeToast =
  | { readonly id: number; readonly kind: "continued"; readonly node: TreeNode; readonly undo: boolean; readonly refusal: StudioApiError | null }
  | { readonly id: number; readonly kind: "accepted"; readonly stage: string }
  | { readonly id: number; readonly kind: "undone" };

type ToastBody = DesignTreeToast extends infer Toast ? Toast extends unknown ? Omit<Toast, "id"> : never : never;

export interface DesignTreeData {
  /** The runtime serves what the tree reads; without it there is no chip and no tree. */
  readonly available: boolean;
  /** The runtime reports admitted Candidates (#294); without it the tree has Stages, Current and running work only. */
  readonly admissions: boolean;
  readonly status: "loading" | "ready" | "failed";
  readonly source: DesignTreeSource | null;
  readonly tree: GrowthTree | null;
  readonly error: StudioApiError | null;
  /** The runtime can move the Working Head. */
  readonly canContinue: boolean;
  readonly canReview: boolean;
  readonly busy: DesignTreeAction | null;
  readonly outcome: DesignTreeOutcome | null;
  /** The last act's confirmation, until it has had its time (FN-5). */
  readonly toast: DesignTreeToast | null;
  reload(): void;
  continueFrom(nodeId: string): Promise<boolean>;
  acceptCurrent(): Promise<boolean>;
  review(nodeId: string, action: "reject" | "archive" | "restore" | "endorse", reason?: string): Promise<boolean>;
  /** Puts back the Current the last Continue replaced, through the same Continue write. */
  undo(): Promise<boolean>;
  clearOutcome(): void;
  /** The toast with this id has had its time; its Undo goes with it. */
  dismissToast(id: number): void;
}

const projectChanged = () => new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED",
  detail: "The design tree belongs to another project. Reconnect this project's runtime before continuing." });

/**
 * The one Continue write, from the working position as read now. Modeling's
 * unrecorded edits hold it back; a write that lost a race with another window
 * or an autosave reads again, up to three times. Resolves to the position it
 * wrote over, which names the Current it replaced.
 */
async function moveHead(studio: StudioClient, projectId: string,
  request: (position: WorkingDraftDto) => WorkingDraftSelectionDto): Promise<WorkingDraftDto> {
  for (let attempt = 1; ; attempt += 1) {
    const position = await studio.workingDraft();
    if (position.projectId !== projectId) throw projectChanged();
    if (position.localDraft) {
      throw new StudioApiError({ status: 0, code: DESIGN_TREE_UNSYNCED, detail: "Unsynced model edits are retained on the current source." });
    }
    const body = request(position);
    try {
      await studio.selectWorkingDraft(body);
      return position;
    } catch (cause) {
      // Another window or an autosave moved the position between the read and this write.
      if (asStudioApiError(cause).code !== "WORKING_DRAFT_STALE" || attempt >= 3) throw cause;
    }
  }
}

/** Design history of every line, the working source and the Worktree Graph, read as one source. */
export async function readDesignTreeSource(studio: StudioClient, projectId: string, signal?: AbortSignal): Promise<DesignTreeSource> {
  const workingSource = await studio.workingSource("modeling", signal);
  if (workingSource.projectId !== projectId) throw projectChanged();
  const line = workingSource.head?.branchId ?? "main";
  const [history, worktrees] = await Promise.all([
    studio.designHistory(line, signal).catch((cause) => {
      if (line === "main") throw cause;
      return studio.designHistory("main", signal);
    }),
    studio.worktrees(signal).catch(() => null),
  ]);
  if (history.projectId !== projectId || (worktrees && worktrees.projectId !== projectId)) throw projectChanged();
  // A future that a fork left behind lives on another line: read those Stages too.
  const others = await Promise.all(history.branches.filter((branch) => branch.branchId !== history.branchId)
    .map((branch) => studio.designHistory(branch.branchId, signal).catch(() => null)));
  // A runtime from before #294 answers without the admission arrays: read them as empty.
  const stages = new Map(history.stages.map((stage) => [stage.stageRef, stage]));
  const candidates = new Map((history.candidates ?? []).map((candidate) => [candidate.candidateId, candidate]));
  const studies = new Map((history.studies ?? []).map((study) => [study.id, study]));
  const warnings = new Set(history.warnings ?? []);
  for (const other of others) {
    if (!other || other.projectId !== projectId) continue;
    for (const stage of other.stages) if (!stages.has(stage.stageRef)) stages.set(stage.stageRef, stage);
    for (const candidate of other.candidates ?? []) if (!candidates.has(candidate.candidateId)) candidates.set(candidate.candidateId, candidate);
    for (const study of other.studies ?? []) if (!studies.has(study.id)) studies.set(study.id, study);
    for (const warning of other.warnings ?? []) warnings.add(warning);
  }
  const merged: DesignHistoryDto = { ...history, stages: [...stages.values()], candidates: [...candidates.values()],
    studies: [...studies.values()], warnings: [...warnings] };
  return { projectId, history: merged, workingSource, worktrees };
}

export function useDesignTree({ studio, capabilities, projectId, active, refreshKey, onHeadMoved }: {
  studio: StudioClient;
  capabilities: readonly string[] | null;
  projectId: string | null | undefined;
  active: boolean;
  refreshKey: number;
  /** The Working Head moved or a Stage was accepted: workspaces that follow it should read it again. */
  onHeadMoved(): void;
}): DesignTreeData {
  const available = Boolean(projectId) && capabilities !== null &&
    capabilities.includes("design-history") && capabilities.includes("working-source");
  const canContinue = available && capabilities!.includes("working-draft");
  const admissions = available && capabilities!.includes("candidate-admission");
  const canReview = available && capabilities!.includes("candidate-review");
  const [source, setSource] = useState<DesignTreeSource | null>(null);
  const [status, setStatus] = useState<DesignTreeData["status"]>("loading");
  const [error, setError] = useState<StudioApiError | null>(null);
  const [busy, setBusy] = useState<DesignTreeAction | null>(null);
  const [outcome, setOutcome] = useState<DesignTreeOutcome | null>(null);
  const [toast, setToast] = useState<DesignTreeToast | null>(null);
  const [nudge, setNudge] = useState(0);
  const reads = useRef(0);
  const busyRef = useRef(false);
  const headMoved = useRef(onHeadMoved);
  headMoved.current = onHeadMoved;
  // The Current the last Continue replaced, recorded before its write; it lives as long as that Continue's toast.
  const undoable = useRef<ContinueUndo | null>(null);
  const toastIds = useRef(0);
  const shownToast = useRef<DesignTreeToast | null>(null);
  shownToast.current = toast;

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!available || !projectId) return;
    const read = ++reads.current;
    try {
      const next = await readDesignTreeSource(studio, projectId, signal);
      if (read !== reads.current || signal?.aborted) return;
      setSource(next); setError(null); setStatus("ready");
    } catch (cause) {
      if (read !== reads.current || signal?.aborted) return;
      setError(asStudioApiError(cause));
      setStatus((previous) => previous === "ready" ? previous : "failed");
    }
  }, [available, projectId, studio]);

  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [available, load, refreshKey, nudge]);

  useEffect(() => {
    if (!available || !active) return;
    const refresh = () => { if (!document.hidden && !busyRef.current) void load(); };
    const timer = window.setInterval(refresh, DESIGN_TREE_POLL_MS);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [available, active, load]);

  const tree = useMemo(() => source ? buildGrowthTree(source) : null, [source]);

  const run = useCallback(async (action: DesignTreeAction,
    write: () => Promise<{ outcome: DesignTreeOutcome | null; toast: ToastBody; undo: ContinueUndo | null }>) => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(action);
    // A new act replaces the last one's outcome, toast and Undo; an Undo answers in its own toast.
    if (action.kind !== "undo") { setOutcome(null); setToast(null); undoable.current = null; }
    try {
      const done = await write();
      setOutcome(done.outcome);
      undoable.current = done.undo;
      setToast({ ...done.toast, id: ++toastIds.current } as DesignTreeToast);
      headMoved.current();
      await load();
      return true;
    } catch (cause) {
      const error = asStudioApiError(cause);
      if (action.kind === "undo") {
        // A refused Undo keeps the toast it was asked from, which now says why; there is nothing left to undo.
        undoable.current = null;
        const id = ++toastIds.current;
        setToast((last) => last?.kind === "continued" ? { ...last, id, undo: false, refusal: error } : last);
      } else setOutcome({ kind: "refused", node: action.kind === "continue" ? action.node : "current", error });
      return false;
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }, [load]);

  const continueFrom = useCallback((nodeId: string) => {
    const node = tree?.nodes.get(nodeId);
    if (!canContinue || !projectId || !node?.runId || (node.kind !== "candidate" && node.kind !== "stage")) return Promise.resolve(false);
    const target: HeadTarget = { runId: node.runId, branchId: node.kind === "stage" ? node.stage!.branchId : null };
    return run({ kind: "continue", node: nodeId }, async () => {
      const replaced = await moveHead(studio, projectId, (position) => continueRequest(projectId, target, position));
      // The previous Current is what this write replaced: recorded before it, from the position it read.
      const undo = continueUndo(replaced, target.runId);
      return { outcome: { kind: "continued", node: nodeId }, toast: { kind: "continued", node, undo: undo !== null, refusal: null }, undo };
    });
  }, [canContinue, projectId, run, studio, tree]);

  const acceptCurrent = useCallback(() => {
    const accept = tree?.accept;
    if (!projectId || !accept?.allowed || !accept.candidateId || !accept.branchId || !accept.expectedHeadStageRef) return Promise.resolve(false);
    return run({ kind: "accept" }, async () => {
      const stage = await studio.acceptCandidate(accept.candidateId!, { projectId, branchId: accept.branchId!,
        expectedHeadStageRef: accept.expectedHeadStageRef! });
      // The toast names the Stage as the confirm did ("Accept as S3"); acceptance cannot be undone.
      return { outcome: { kind: "accepted", stageLabel: stage.label }, toast: { kind: "accepted", stage: accept.nextLabel ?? stage.label }, undo: null };
    });
  }, [projectId, run, studio, tree]);

  const review = useCallback(async (nodeId: string, action: "reject" | "archive" | "restore" | "endorse", reason?: string) => {
    const node = tree?.nodes.get(nodeId);
    if (!canReview || !projectId || !node || (node.kind !== "candidate" && node.kind !== "stage") || busyRef.current) return false;
    busyRef.current = true; setBusy({ kind: "review", node: nodeId }); setOutcome(null);
    try {
      await studio.reviewCandidate({ projectId, subjectKind: node.kind,
        subjectRef: node.kind === "candidate" ? node.candidate!.candidateId : node.stage!.ref,
        action, reason: reason?.trim() || null });
      await load(); return true;
    } catch (cause) {
      setOutcome({ kind: "refused", node: nodeId, error: asStudioApiError(cause) }); return false;
    } finally { busyRef.current = false; setBusy(null); }
  }, [canReview, load, projectId, studio, tree]);

  const undo = useCallback(() => {
    const last = undoable.current;
    if (!last || !canContinue || !projectId) return Promise.resolve(false);
    return run({ kind: "undo" }, async () => {
      await moveHead(studio, projectId, (position) => {
        const request = undoRequest(projectId, last, position);
        if (!request) {
          throw new StudioApiError({ status: 0, code: DESIGN_TREE_UNDO_MOVED, detail: "Current moved on after the Continue; nothing was undone." });
        }
        return request;
      });
      return { outcome: null, toast: { kind: "undone" }, undo: null };
    });
  }, [canContinue, projectId, run, studio]);

  const dismissToast = useCallback((id: number) => {
    if (shownToast.current?.id !== id) return;
    undoable.current = null;
    setToast(null);
  }, []);

  return {
    available, admissions, status: available ? status : "loading", source, tree, error, canContinue, canReview, busy, outcome, toast,
    reload: () => setNudge((value) => value + 1),
    continueFrom, acceptCurrent, review, undo,
    clearOutcome: () => setOutcome(null),
    dismissToast,
  };
}

/**
 * Record edits and continue (#302), wherever a Continue was refused because
 * Modeling holds unrecorded edits: Modeling records them, then the same
 * Continue runs again. `record` is Modeling's recorder, when it is open.
 */
export function useRecordAndContinue(data: DesignTreeData, record: (() => Promise<void>) | null) {
  const [recording, setRecording] = useState(false);
  const [failure, setFailure] = useState<StudioApiError | null>(null);
  const recordAndContinue = async (nodeId: string) => {
    if (!record || recording) return;
    setRecording(true); setFailure(null);
    try { await record(); }
    catch (cause) { setFailure(asStudioApiError(cause)); return; }
    finally { setRecording(false); }
    await data.continueFrom(nodeId);
  };
  return { recording, failure, recordAndContinue };
}

const SEEN_KEY = "monkeyhub.design-tree.seen.v1";
const SEEN_LIMIT = 400;

/** Which admitted options this viewer has opened: per-viewer memory, not project state. */
export function useSeenCandidates(projectId: string | null | undefined) {
  const read = useCallback((): Set<string> => {
    if (!projectId) return new Set();
    try {
      const saved = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "{}") as Record<string, unknown>;
      const list = saved[projectId];
      return new Set(Array.isArray(list) ? list.filter((item): item is string => typeof item === "string") : []);
    } catch { return new Set(); }
  }, [projectId]);
  const [seen, setSeen] = useState<ReadonlySet<string>>(read);
  useEffect(() => setSeen(read()), [read]);
  const markSeen = useCallback((candidateId: string) => {
    if (!projectId) return;
    setSeen((previous) => {
      if (previous.has(candidateId)) return previous;
      const next = new Set(previous).add(candidateId);
      try {
        const saved = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "{}") as Record<string, unknown>;
        saved[projectId] = [...next].slice(-SEEN_LIMIT);
        window.localStorage.setItem(SEEN_KEY, JSON.stringify(saved));
      } catch { /* Losing this memory only brings the "new" marks back. */ }
      return next;
    });
  }, [projectId]);
  return { seen, markSeen };
}
