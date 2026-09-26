/**
 * The Design Tree as one growing branch, built from retained project facts.
 *
 * Stage → Study → admitted Candidates → Continue → next Stage (#284). The
 * Working Head's lineage is the trunk: the root, each Stage and each option
 * that was chosen and deepened, and Current at the tip. Every other admitted
 * Candidate is a twig off the point it started from, and running or queued
 * Agent work is a placeholder there. A future that a later Continue left
 * behind stays in the tree; it is simply no longer the trunk.
 *
 * Only facts become nodes: Stages from design history, admitted Candidates
 * from the #294 contract, running lines from the Worktree Graph and the
 * Working Head from the working source. Runs, repairs and results rejected
 * before admission never do. This module is pure and has no copy: the
 * surfaces word it.
 */
import type { AdmissionActorDto, DesignCandidateDto, DesignStageDto, DesignStudyDto, ReviewJudgementDto, WorkingHeadDto, WorktreeLineDto } from "../../api/generated";
import type { DesignTreeSource } from "./contract";

export type TreeNodeKind = "origin" | "stage" | "candidate" | "pending" | "current";

export interface StageFacts {
  readonly ref: string;
  /** Position on its line: S0 is the first accepted Stage. */
  readonly number: number;
  /** The accepted label when it says more than the S-number, such as "Layout". */
  readonly name: string | null;
  readonly branchId: string;
  readonly acceptedBy: string;
  readonly acceptedAt: string | null;
  /** The run accepted as this Stage: the one option that is it, not an ancestor it grew from. */
  readonly candidateId: string;
  readonly review: ReviewJudgementDto | null;
}

export interface CandidateFacts {
  readonly candidateId: string;
  readonly review: ReviewJudgementDto | null;
  readonly admittedBy: string | null;
  readonly admittedOrigin: string | null;
  readonly admittedAt: string | null;
  readonly legacy: DesignCandidateDto["legacy"];
  readonly baseStageRef: string | null;
  /** The Stage later accepted from this Candidate's line, as its node id. */
  readonly acceptedStage: string | null;
  /** Review checks that did not hold when a person admitted it for comparison (#294 Q2); empty when review-ready. */
  readonly blockedBy: readonly string[];
}

export type PendingStatus = "running" | "queued" | "interrupted";

export interface PendingFacts {
  readonly lineId: string;
  readonly status: PendingStatus;
  readonly detail: string | null;
  readonly updatedAt: string | null;
}

export interface CurrentFacts {
  readonly headRunId: string;
  /** Retained runs between the node Current hangs from and the head itself. */
  readonly editsAfter: number;
  /** True when the head is exactly an accepted Stage's model. */
  readonly accepted: boolean;
  readonly sourceDisposition: ReviewJudgementDto["disposition"] | null;
}

export interface TreeNode {
  readonly id: string;
  readonly kind: TreeNodeKind;
  readonly parent: string | null;
  /** The model run the node stands for; null for the project start. */
  readonly runId: string | null;
  /** Main copy from the facts themselves; the surfaces word the kinds that have none. */
  readonly label: string | null;
  readonly summary: string | null;
  /** A stable letter within a Study, in the order the options were asked for. */
  readonly letter: string | null;
  readonly studyId: string | null;
  readonly stage?: StageFacts;
  readonly candidate?: CandidateFacts;
  readonly pending?: PendingFacts;
  readonly current?: CurrentFacts;
}

export interface TreeStudy {
  readonly id: string;
  readonly label: string | null;
  /** Its admitted options as node ids, in letter order. */
  readonly members: readonly string[];
}

/** Why Current cannot be accepted as the next Stage right now. */
export type AcceptBlock = "no-head" | "already-stage" | "no-stage" | "rejected" | "older-stage";

export interface AcceptState {
  readonly allowed: boolean;
  readonly block: AcceptBlock | null;
  /** What the existing accept route needs: the head run, its line and that line's head Stage. */
  readonly candidateId: string | null;
  readonly branchId: string | null;
  readonly expectedHeadStageRef: string | null;
  /** The label the next Stage will get, such as "S3". */
  readonly nextLabel: string | null;
  /** For "older-stage": the Stage Current works from, and the line's newest one. */
  readonly baseStage: string | null;
  readonly lineHeadStage: string | null;
}

export interface GrowthTree {
  readonly nodes: ReadonlyMap<string, TreeNode>;
  /** Children in drawing order: options by Study and letter, then Stages, then running work. */
  readonly children: ReadonlyMap<string, readonly string[]>;
  readonly studies: ReadonlyMap<string, TreeStudy>;
  readonly root: string;
  /** The Working Head's lineage from the root, ending at "current". */
  readonly trunk: readonly string[];
  readonly onTrunk: ReadonlySet<string>;
  /** Candidates that a line continued: towards Current, or to an accepted Stage. */
  readonly continued: ReadonlySet<string>;
  /** The Stage Current works from, as a node id. */
  readonly currentStage: string | null;
  readonly accept: AcceptState;
  readonly counts: { readonly running: number; readonly queued: number; readonly interrupted: number };
  /** Admitted options on Current's Stage that are not on the trunk; the viewer's "new" filter comes later. */
  readonly freshCandidates: readonly string[];
  /** Rejected or archived admitted Candidates omitted from this projection. */
  readonly processedCount: number;
}

export const CURRENT = "current";
export const ORIGIN = "origin";
export const stageNodeId = (ref: string) => `stage:${ref}`;
export const candidateNodeId = (id: string) => `candidate:${id}`;
export const pendingNodeId = (lineId: string) => `pending:${lineId}`;

const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const letterAt = (index: number) => index < LETTERS.length ? LETTERS[index] : `${LETTERS[index % LETTERS.length]}${Math.floor(index / LETTERS.length)}`;
const actorOf = (value: AdmissionActorDto | null | undefined) => ({ actor: value?.actorId ?? null, origin: value?.origin ?? null });
const PENDING: ReadonlySet<string> = new Set(["running", "queued", "interrupted"]);

/** The growth tree of one project, from what its runtime retained. */
export function buildGrowthTree(source: DesignTreeSource, includeProcessed = false): GrowthTree {
  const { history, workingSource, worktrees } = source;
  const head: WorkingHeadDto | null = workingSource.head ?? null;
  const nodes = new Map<string, TreeNode>();
  const parents = new Map<string, string | null>();

  // Stages, numbered by their place on their own line.
  const stageDtos = new Map<string, DesignStageDto>();
  for (const stage of history.stages) if (!stageDtos.has(stage.stageRef)) stageDtos.set(stage.stageRef, stage);
  const depth = new Map<string, number>();
  const depthOf = (ref: string, seen = new Set<string>()): number => {
    const known = depth.get(ref);
    if (known !== undefined) return known;
    const parent = stageDtos.get(ref)?.parentStageRef ?? null;
    const value = parent && stageDtos.has(parent) && !seen.has(parent) ? depthOf(parent, seen.add(ref)) + 1 : 0;
    depth.set(ref, value);
    return value;
  };
  const stageByRun = new Map<string, string>();
  for (const stage of stageDtos.values()) {
    const number = depthOf(stage.stageRef);
    const id = stageNodeId(stage.stageRef);
    const name = stage.label && stage.label !== `S${number}` ? stage.label : null;
    nodes.set(id, { id, kind: "stage", parent: null, runId: stage.modelSource.runId, label: name, summary: null, letter: null, studyId: null,
      stage: { ref: stage.stageRef, number, name, branchId: stage.branchId, acceptedBy: stage.acceptedBy,
        acceptedAt: stage.acceptance?.occurredAt ?? null, candidateId: stage.candidateId, review: stage.review ?? null } });
    stageByRun.set(stage.candidateId, id);
    stageByRun.set(stage.modelSource.runId, id);
  }
  const stageLabel = (ref: string | null | undefined) => (ref && nodes.has(stageNodeId(ref)) ? stageNodeId(ref) : null);

  // Admitted Candidates. A Stage's own run admitted only because it was
  // accepted (legacy "stage") is that Stage, not a second node.
  const uniqueCandidates = (history.candidates ?? []).filter((candidate, index, all) => candidate.outcome !== "rejected" &&
    all.findIndex((other) => other.candidateId === candidate.candidateId) === index &&
    !(candidate.legacy === "stage" && stageByRun.has(candidate.candidateId)));
  const isProcessed = (candidate: DesignCandidateDto) => candidate.review?.disposition === "rejected" || candidate.review?.disposition === "archived";
  // An accepted Candidate is part of the immutable Stage lineage even if it was
  // reviewed later. It is never treated as a disposable processed option.
  const processedCount = uniqueCandidates.filter((candidate) => isProcessed(candidate) && !candidate.acceptedStageRef).length;
  const candidates = uniqueCandidates.filter((candidate) => includeProcessed || !isProcessed(candidate) || Boolean(candidate.acceptedStageRef));
  const studyDtos = new Map<string, DesignStudyDto>();
  for (const study of history.studies ?? []) if (!studyDtos.has(study.id)) studyDtos.set(study.id, study);
  const candidateByRun = new Map<string, string>();
  for (const candidate of candidates) candidateByRun.set(candidate.candidateId, candidateNodeId(candidate.candidateId));
  const allCandidateByRun = new Map(uniqueCandidates.map((candidate) => [candidate.candidateId, candidateNodeId(candidate.candidateId)]));

  // Studies keep the order their options were asked for; letters follow it.
  const members = new Map<string, string[]>();
  for (const study of studyDtos.values()) {
    members.set(study.id, (study.candidateIds ?? []).filter((id) => allCandidateByRun.has(id)).map(candidateNodeId));
  }
  for (const candidate of uniqueCandidates) {
    if (!candidate.studyId) continue;
    const list = members.get(candidate.studyId) ?? [];
    if (!list.includes(candidateNodeId(candidate.candidateId))) list.push(candidateNodeId(candidate.candidateId));
    members.set(candidate.studyId, list);
  }
  // A letter tells options apart: a Study of one option needs none.
  const letters = new Map<string, string>();
  for (const list of members.values()) if (list.length > 1) list.forEach((id, index) => letters.set(id, letterAt(index)));

  // A run that is both an option and a Stage's model is that Stage from then on:
  // the Stage is the later point on its line.
  const nodeOfRun = (run: string | null | undefined): string | undefined => run ? stageByRun.get(run) ?? candidateByRun.get(run) : undefined;
  const resolveRef = (ref: string | null | undefined): string | null => ref ? nodeOfRun(ref) ?? stageLabel(ref) : null;
  for (const candidate of candidates) {
    const id = candidateNodeId(candidate.candidateId);
    const study = candidate.studyId ? studyDtos.get(candidate.studyId) : undefined;
    const { actor, origin } = actorOf(candidate.admittedBy);
    nodes.set(id, { id, kind: "candidate", parent: null, runId: candidate.candidateId, label: candidate.label?.trim() || null,
      summary: candidate.summary?.trim() || null, letter: letters.get(id) ?? null, studyId: candidate.studyId ?? null,
      candidate: { candidateId: candidate.candidateId, admittedBy: actor, admittedOrigin: origin, admittedAt: candidate.admittedAt ?? null,
        legacy: candidate.legacy ?? null, baseStageRef: candidate.baseStageRef ?? null, acceptedStage: stageLabel(candidate.acceptedStageRef),
        blockedBy: [...(candidate.blockedBy ?? [])], review: candidate.review ?? null } });
    // Where the option grew from: the option it continued, else its Study's
    // start (its base run when that is on the tree, else its Stage), else the
    // Stage its work began on.
    const parent = [resolveRef(candidate.continuedFrom), nodeOfRun(study?.baseRunId) ?? null, stageLabel(study?.baseStageRef),
      stageLabel(candidate.baseStageRef)].find((value) => value !== null && value !== id) ?? null;
    parents.set(id, parent);
  }
  // A continuedFrom chain that loops back names no parent at all.
  for (const id of candidateByRun.values()) {
    const seen = new Set<string>([id]);
    for (let cursor = parents.get(id) ?? null; cursor !== null && nodes.get(cursor)?.kind === "candidate"; cursor = parents.get(cursor) ?? null) {
      if (seen.has(cursor)) { parents.set(id, stageLabel(nodes.get(id)!.candidate!.baseStageRef)); break; }
      seen.add(cursor);
    }
  }

  // A Stage grows from the nearest admitted option its accepted run descends
  // from; without one, from the Stage before it.
  const ancestors = (id: string) => {
    const out = new Set<string>();
    for (let cursor = parents.get(id) ?? null; cursor !== null && !out.has(cursor); cursor = parents.get(cursor) ?? null) out.add(cursor);
    return out;
  };
  for (const stage of stageDtos.values()) {
    const id = stageNodeId(stage.stageRef);
    const claims = candidates.filter((candidate) => candidate.acceptedStageRef === stage.stageRef)
      .map((candidate) => candidateNodeId(candidate.candidateId));
    const nearest = claims.find((claim) => !claims.some((other) => other !== claim && ancestors(other).has(claim))) ?? null;
    parents.set(id, nearest ?? stageLabel(stage.parentStageRef));
  }

  // Current hangs from the first node its own lineage reaches.
  const lineage = head?.lineage?.length ? head.lineage : head ? [head.runId] : [];
  let anchor: string | null = null, editsAfter = 0;
  for (const [index, runId] of lineage.entries()) {
    const found = nodeOfRun(runId);
    if (found) { anchor = found; editsAfter = index; break; }
  }
  if (anchor === null && head) {
    anchor = stageLabel(head.sourceStageRef);
    editsAfter = lineage.length;
  }
  if (head) {
    const sourceDisposition = uniqueCandidates.find((candidate) => candidate.candidateId === head.runId)?.review?.disposition ?? null;
    nodes.set(CURRENT, { id: CURRENT, kind: "current", parent: null, runId: head.runId, label: head.label ?? null, summary: null,
      letter: null, studyId: null, current: { headRunId: head.runId, editsAfter, accepted: head.accepted, sourceDisposition } });
    parents.set(CURRENT, anchor);
  }

  // Running and queued work waits where it started.
  const runningLines = (worktrees?.lines ?? []).filter((line): line is WorktreeLineDto & { status: PendingStatus } =>
    line.kind === "running" && PENDING.has(line.status));
  for (const line of runningLines) {
    const id = pendingNodeId(line.lineId);
    const base = line.baseRunId;
    let parent: string | null;
    if (head && base === head.runId) parent = anchor !== null && nodes.get(anchor)?.runId === head.runId ? anchor : CURRENT;
    else parent = nodeOfRun(base) ?? (head && base && lineage.includes(base) ? CURRENT : null) ??
      stageLabel(line.baseStageRef) ?? (head ? CURRENT : null);
    nodes.set(id, { id, kind: "pending", parent: null, runId: line.runId, label: line.label?.trim() || null, summary: line.detail?.trim() || null,
      letter: null, studyId: line.studyId ?? null,
      pending: { lineId: line.lineId, status: line.status, detail: line.detail ?? null, updatedAt: line.updatedAt ?? null } });
    parents.set(id, parent);
  }

  // Facts that point at each other in a circle name no parent: the loop is cut
  // where it closes, and that node grows from the root instead.
  for (const id of nodes.keys()) {
    const walked = new Set<string>();
    for (let cursor: string | null = id; cursor !== null; cursor = parents.get(cursor) ?? null) {
      if (walked.has(cursor)) { parents.set(cursor, null); break; }
      walked.add(cursor);
    }
  }

  // One root: the first Stage when it alone has no parent, else the project start.
  const orphans = [...nodes.keys()].filter((id) => (parents.get(id) ?? null) === null);
  let root: string;
  if (orphans.length === 1 && nodes.get(orphans[0])!.kind === "stage") root = orphans[0];
  else {
    root = ORIGIN;
    nodes.set(ORIGIN, { id: ORIGIN, kind: "origin", parent: null, runId: null, label: null, summary: null, letter: null, studyId: null });
    for (const id of orphans) parents.set(id, ORIGIN);
  }
  parents.set(root, null);

  const finalNodes = new Map<string, TreeNode>();
  for (const [id, node] of nodes) finalNodes.set(id, { ...node, parent: parents.get(id) ?? null });

  // Children in drawing order.
  const rank = (id: string) => {
    const node = finalNodes.get(id)!;
    if (node.kind === "candidate") {
      const study = node.studyId ? [...members.keys()].indexOf(node.studyId) : members.size;
      const letter = node.studyId ? (members.get(node.studyId) ?? []).indexOf(id) : 0;
      return [0, study, letter];
    }
    return [node.kind === "stage" ? 1 : node.kind === "pending" ? 2 : 3, 0, 0];
  };
  const children = new Map<string, string[]>();
  for (const [id, node] of finalNodes) {
    if (node.parent === null) continue;
    const list = children.get(node.parent) ?? [];
    list.push(id);
    children.set(node.parent, list);
  }
  for (const list of children.values()) list.sort((a, b) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2] || a.localeCompare(b);
  });

  const trunk: string[] = [];
  if (head) {
    for (let cursor: string | null = CURRENT; cursor !== null; cursor = finalNodes.get(cursor)?.parent ?? null) {
      if (trunk.includes(cursor)) break;
      trunk.unshift(cursor);
    }
  } else trunk.push(root);
  const onTrunk = new Set(trunk);

  // An option is continued when a line runs through it: to Current, or to a Stage.
  const continued = new Set<string>();
  const markLine = (from: string) => {
    const walked = new Set<string>();
    for (let cursor = finalNodes.get(from)?.parent ?? null; cursor !== null && !walked.has(cursor); cursor = finalNodes.get(cursor)?.parent ?? null) {
      walked.add(cursor);
      if (finalNodes.get(cursor)?.kind === "candidate") continued.add(cursor);
    }
  };
  if (head) markLine(CURRENT);
  for (const [id, node] of finalNodes) if (node.kind === "stage") markLine(id);

  const studies = new Map<string, TreeStudy>();
  for (const [id, list] of members) studies.set(id, { id, label: studyDtos.get(id)?.label?.trim() || null,
    members: list.filter((member) => finalNodes.has(member)) });

  const currentStage = head ? stageLabel(head.sourceStageRef) : null;
  const counts = { running: 0, queued: 0, interrupted: 0 };
  for (const line of runningLines) counts[line.status] += 1;
  const freshCandidates = [...finalNodes.values()].filter((node) => node.kind === "candidate" && !onTrunk.has(node.id) &&
    !continued.has(node.id) && currentStage !== null && stageLabel(node.candidate!.baseStageRef) === currentStage).map((node) => node.id);

  return { nodes: finalNodes, children, studies, root, trunk, onTrunk, continued, currentStage,
    accept: acceptState(source, head, finalNodes), counts, freshCandidates, processedCount };
}

function acceptState(source: DesignTreeSource, head: WorkingHeadDto | null, nodes: ReadonlyMap<string, TreeNode>): AcceptState {
  const refused = (block: AcceptBlock, extra: Partial<AcceptState> = {}): AcceptState => ({ allowed: false, block, candidateId: null,
    branchId: null, expectedHeadStageRef: null, nextLabel: null, baseStage: null, lineHeadStage: null, ...extra });
  if (!head) return refused("no-head");
  const history = source.history;
  const branch = history.branches.find((item) => item.branchId === (head.branchId ?? history.branchId)) ??
    history.branches.find((item) => item.branchId === history.branchId);
  if (!branch) return refused("no-stage");
  const lineHead = nodes.get(stageNodeId(branch.headStageRef));
  const nextLabel = lineHead?.stage ? `S${lineHead.stage.number + 1}` : null;
  if (head.accepted) return refused("already-stage", { lineHeadStage: lineHead?.id ?? null, nextLabel });
  // The runtime refuses a run the architect turned down (409 CANDIDATE_REJECTED); say so before asking.
  const verdict = source.worktrees?.lines.find((line) => line.kind === "head" && line.runId === head.runId)?.admission;
  if (verdict === "rejected") return refused("rejected", { lineHeadStage: lineHead?.id ?? null, nextLabel });
  if ((head.sourceStageRef ?? null) !== branch.headStageRef) {
    return refused("older-stage", { baseStage: head.sourceStageRef ? stageNodeId(head.sourceStageRef) : null,
      lineHeadStage: lineHead?.id ?? null, nextLabel });
  }
  return { allowed: true, block: null, candidateId: head.runId, branchId: branch.branchId, expectedHeadStageRef: branch.headStageRef,
    nextLabel, baseStage: stageNodeId(branch.headStageRef), lineHeadStage: lineHead?.id ?? null };
}

/** The trunk's identity: when it changes, the view is fitted again. */
export const trunkKey = (tree: GrowthTree) => tree.trunk.join("|");
