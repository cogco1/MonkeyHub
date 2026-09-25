/**
 * One project's Design Tree: its retained facts, read together, and the two
 * actions that change them through the routes that already own them.
 *
 * Continue is the existing working-position write (`PUT /api/working-draft`,
 * the path `changeEditingBase` takes); Accept as next Stage is the existing
 * `POST /api/candidates/{id}/accept`, on the Working Head only. View changes
 * nothing here. The facts are read again on a short interval while the
 * workspace is on screen, on focus, and after each action.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { asStudioApiError, StudioApiError, type StudioClient } from "../../api/client";
import type { DesignHistoryDto } from "../../api/generated";
import type { DesignTreeSource } from "./contract";
import { buildGrowthTree, type GrowthTree } from "./model";

/** How often an open project re-reads its tree while it is on screen. */
export const DESIGN_TREE_POLL_MS = 10_000;

/** A retained local draft belongs to its exact source; Continue waits until it is synced or undone. */
export const DESIGN_TREE_UNSYNCED = "DESIGN_TREE_UNSYNCED_EDITS";

export type DesignTreeAction = { readonly kind: "continue"; readonly node: string } | { readonly kind: "accept" };

export interface DesignTreeOutcome {
  readonly kind: "continued" | "accepted" | "refused";
  readonly node?: string;
  readonly stageLabel?: string;
  readonly error?: StudioApiError;
}

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
  readonly busy: DesignTreeAction | null;
  readonly outcome: DesignTreeOutcome | null;
  reload(): void;
  continueFrom(nodeId: string): Promise<boolean>;
  acceptCurrent(): Promise<boolean>;
  clearOutcome(): void;
}

const projectChanged = () => new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED",
  detail: "The design tree belongs to another project. Reconnect this project's runtime before continuing." });

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
  const [source, setSource] = useState<DesignTreeSource | null>(null);
  const [status, setStatus] = useState<DesignTreeData["status"]>("loading");
  const [error, setError] = useState<StudioApiError | null>(null);
  const [busy, setBusy] = useState<DesignTreeAction | null>(null);
  const [outcome, setOutcome] = useState<DesignTreeOutcome | null>(null);
  const [nudge, setNudge] = useState(0);
  const reads = useRef(0);
  const busyRef = useRef(false);
  const headMoved = useRef(onHeadMoved);
  headMoved.current = onHeadMoved;

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

  const run = useCallback(async (action: DesignTreeAction, write: () => Promise<DesignTreeOutcome>) => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(action); setOutcome(null);
    try {
      const done = await write();
      setOutcome(done);
      headMoved.current();
      await load();
      return true;
    } catch (cause) {
      setOutcome({ kind: "refused", node: action.kind === "continue" ? action.node : "current", error: asStudioApiError(cause) });
      return false;
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }, [load]);

  const continueFrom = useCallback((nodeId: string) => {
    const node = tree?.nodes.get(nodeId);
    if (!canContinue || !projectId || !node?.runId || (node.kind !== "candidate" && node.kind !== "stage")) return Promise.resolve(false);
    return run({ kind: "continue", node: nodeId }, async () => {
      for (let attempt = 1; ; attempt += 1) {
        const position = await studio.workingDraft();
        if (position.projectId !== projectId) throw projectChanged();
        if (position.localDraft) {
          throw new StudioApiError({ status: 0, code: DESIGN_TREE_UNSYNCED, detail: "Unsynced model edits are retained on the current source." });
        }
        try {
          await studio.selectWorkingDraft({ projectId, runId: node.runId, baseRevisionSha256: position.revisionSha256 ?? null,
            branchId: node.kind === "stage" ? node.stage!.branchId : null });
          return { kind: "continued", node: nodeId };
        } catch (cause) {
          // Another window or an autosave moved the position between the read and this write.
          if (asStudioApiError(cause).code !== "WORKING_DRAFT_STALE" || attempt >= 3) throw cause;
        }
      }
    });
  }, [canContinue, projectId, run, studio, tree]);

  const acceptCurrent = useCallback(() => {
    const accept = tree?.accept;
    if (!projectId || !accept?.allowed || !accept.candidateId || !accept.branchId || !accept.expectedHeadStageRef) return Promise.resolve(false);
    return run({ kind: "accept" }, async () => {
      const stage = await studio.acceptCandidate(accept.candidateId!, { projectId, branchId: accept.branchId!,
        expectedHeadStageRef: accept.expectedHeadStageRef! });
      return { kind: "accepted", stageLabel: stage.label };
    });
  }, [projectId, run, studio, tree]);

  return {
    available, admissions, status: available ? status : "loading", source, tree, error, canContinue, busy, outcome,
    reload: () => setNudge((value) => value + 1),
    continueFrom, acceptCurrent,
    clearOutcome: () => setOutcome(null),
  };
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
