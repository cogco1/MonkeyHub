/**
 * DEV AND TEST FIXTURE · not imported by production code.
 *
 * The #284 prototype's Riverside Library project (docs/prototypes/
 * candidate-graph/data/issue-fixture.js), recast as the Project Runtime facts
 * the Design Tree reads: design history with the #294 `candidates[]` and
 * `studies[]` contract, the working source and the Worktree Graph. It keeps
 * the facts a real runtime keeps (run lineage, Stage acceptance, the working
 * position) and answers Continue and Accept the way the existing routes do,
 * so a stubbed API can serve it until `codex/294-admission-record` lands.
 * Everything in it is invented.
 */
import type { DesignBranchDto, DesignStageDto, WorkingDraftDto, WorkingHeadDto, WorkingSourceDto, WorktreeGraphDto, WorktreeLineDto } from "../../api/generated";
import type { AdmittedCandidateDto, DesignHistoryReading, DesignStudyDto } from "./contract";

export const FIXTURE_PROJECT = "riverside-library";
const ref = (run: string, record = "design-stage") => `project://${FIXTURE_PROJECT}/runs/${run}/review/${record}.json`;
const digest = (value: string) => [...value].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 7).toString(16).padStart(8, "0").repeat(8);

interface RunFact {
  readonly parent: string | null;
  /** The Stage this run's work started from (its delta's source Stage). */
  readonly sourceStage: string | null;
}

interface StageFact {
  readonly ref: string;
  readonly parent: string | null;
  readonly run: string;
  readonly label: string;
  readonly acceptedBy: string;
  readonly acceptedAt: string;
}

interface CandidateFact {
  readonly run: string;
  readonly label: string;
  readonly summary: string;
  readonly studyId: string | null;
  readonly admittedBy: string;
  readonly admittedAt: string;
  continuedFrom: string | null;
  acceptedStage: string | null;
}

interface RunningFact {
  readonly lineId: string;
  readonly base: string;
  readonly label: string;
  readonly status: "running" | "queued" | "interrupted";
  readonly detail: string | null;
}

export interface DesignTreeFixtureState {
  readonly runs: Map<string, RunFact>;
  readonly stages: StageFact[];
  readonly candidates: CandidateFact[];
  readonly studies: DesignStudyDto[];
  running: RunningFact[];
  branchHead: string;
  head: string;
  revision: number;
}

export class FixtureRefusal extends Error {
  constructor(readonly status: number, readonly code: string, detail: string) { super(detail); }
}

const S0 = ref("run-site"), S1 = ref("run-s1-massing"), S2 = ref("run-s2-layout");

/** A fresh copy of the Riverside Library facts: S0 → Massing C → S1 → Facade B → S2 = Current. */
export function riversideLibraryFacts(): DesignTreeFixtureState {
  const runs = new Map<string, RunFact>([
    ["run-site", { parent: null, sourceStage: null }],
    ...["a", "b", "c", "d", "e"].map((letter) => [`run-massing-${letter}`, { parent: "run-site", sourceStage: S0 }] as const),
    ["run-massing-c-edit", { parent: "run-massing-c", sourceStage: S0 }],
    ["run-s1-massing", { parent: "run-massing-c-edit", sourceStage: S0 }],
    ...["a", "b", "c"].map((letter) => [`run-facade-${letter}`, { parent: "run-s1-massing", sourceStage: S1 }] as const),
    ["run-facade-a2", { parent: "run-facade-a", sourceStage: S1 }],
    ["run-s2-edit-1", { parent: "run-facade-b", sourceStage: S1 }],
    ["run-s2-edit-2", { parent: "run-s2-edit-1", sourceStage: S1 }],
    ["run-s2-layout", { parent: "run-s2-edit-2", sourceStage: S1 }],
    ["run-entrance-a", { parent: "run-s2-layout", sourceStage: S2 }],
  ]);
  const option = (run: string, label: string, summary: string, studyId: string | null, admittedAt: string,
    extra: Partial<Pick<CandidateFact, "continuedFrom" | "acceptedStage" | "admittedBy">> = {}): CandidateFact =>
    ({ run, label, summary, studyId, admittedBy: "Arch Agent", admittedAt, continuedFrom: null, acceptedStage: null, ...extra });
  return {
    runs,
    stages: [
      { ref: S0, parent: null, run: "run-site", label: "Site", acceptedBy: "Kaiwen", acceptedAt: "2026-09-12T10:05:00Z" },
      { ref: S1, parent: S0, run: "run-s1-massing", label: "Massing", acceptedBy: "Kaiwen", acceptedAt: "2026-09-16T17:40:00Z" },
      { ref: S2, parent: S1, run: "run-s2-layout", label: "Layout", acceptedBy: "Kaiwen", acceptedAt: "2026-09-22T18:12:00Z" },
    ],
    candidates: [
      option("run-massing-a", "Slab bar along the river", "One long bar on the setback line; the south half stays a garden.", "study-massing", "2026-09-13T14:26:00Z"),
      option("run-massing-b", "Twin towers on a podium", "Low podium with two reading towers; the strongest skyline.", "study-massing", "2026-09-13T14:29:00Z"),
      option("run-massing-c", "Stepped courtyard block", "Courtyard ring stepping down to the south; best daylight for the reading room.", "study-massing", "2026-09-13T14:31:00Z", { acceptedStage: S1 }),
      option("run-massing-d", "Terraced wedge", "Three terraces rising to the river; the most floor area.", "study-massing", "2026-09-13T14:33:00Z"),
      option("run-massing-e", "Pavilion cluster", "Five linked pavilions around a public garden; the lowest scale.", "study-massing", "2026-09-13T14:36:00Z"),
      option("run-facade-a", "Brick pier rhythm", "Brick piers at 3 m with deep reveals; heavy and quiet.", "study-facade", "2026-09-17T09:48:00Z"),
      option("run-facade-b", "Deep timber fins", "Timber fins at 1.5 m shade the reading room; light and warm.", "study-facade", "2026-09-17T09:52:00Z", { acceptedStage: S2 }),
      option("run-facade-c", "Perforated terracotta screen", "A continuous terracotta screen; the most uniform elevation.", "study-facade", "2026-09-17T09:57:00Z"),
      option("run-facade-a2", "Facade A + 2 edits", "Brick piers with a rooftop reading room; left when B was continued.", null, "2026-09-17T18:40:00Z",
        { continuedFrom: "run-facade-a", admittedBy: "Kaiwen" }),
      option("run-entrance-a", "Courtyard gate on the south bar", "A canopy opens the low south bar; you enter through the courtyard.", "study-entrance", "2026-09-25T21:44:00Z"),
    ],
    studies: [
      { id: "study-massing", label: "Massing Study", baseRunId: "run-site", baseStageRef: S0,
        candidateIds: ["run-massing-a", "run-massing-b", "run-massing-c", "run-massing-d", "run-massing-e"] },
      { id: "study-facade", label: "Facade Study", baseRunId: "run-s1-massing", baseStageRef: S1, candidateIds: ["run-facade-a", "run-facade-b", "run-facade-c"] },
      { id: "study-entrance", label: "Entrance Study", baseRunId: "run-s2-layout", baseStageRef: S2, candidateIds: ["run-entrance-a"] },
    ],
    running: [
      { lineId: "running:job-entrance-b", base: "run-s2-layout", label: "Corner entrance at the south-east", status: "running", detail: "Repairing a stair clash · run 3" },
      { lineId: "running:job-entrance-c", base: "run-s2-layout", label: "River promenade entrance", status: "queued", detail: null },
    ],
    branchHead: S2,
    head: "run-s2-layout",
    revision: 1,
  };
}

/** Reads and writes over one set of facts, in the shapes the runtime answers. */
export function createDesignTreeFixture(state: DesignTreeFixtureState = riversideLibraryFacts()) {
  const lineageOf = (run: string) => {
    const out: string[] = [];
    for (let cursor: string | null = run; cursor !== null && !out.includes(cursor); cursor = state.runs.get(cursor)?.parent ?? null) out.push(cursor);
    return out;
  };
  const stageOfRun = (run: string) => state.stages.find((stage) => stage.run === run) ?? null;
  const revision = () => `rev-${String(state.revision).padStart(4, "0")}`;
  const modelSource = (run: string) => ({ runId: run, stateDigest: digest(`state:${run}`), assetSha256: digest(`model:${run}`) });
  const historyOrder = () => {
    const out: StageFact[] = [];
    for (let cursor: string | null = state.branchHead; cursor !== null; cursor = state.stages.find((stage) => stage.ref === cursor)?.parent ?? null) {
      const stage = state.stages.find((item) => item.ref === cursor);
      if (!stage) break;
      out.unshift(stage);
    }
    return out;
  };
  const head = (): WorkingHeadDto => {
    const stage = stageOfRun(state.head);
    return { runId: state.head, stateDigest: digest(`state:${state.head}`), recordDigest: digest(`record:${state.head}`),
      sourceStageRef: stage ? stage.ref : state.runs.get(state.head)?.sourceStage ?? null, branchId: "main", accepted: stage !== null,
      origin: "working-position", label: null, modelSource: modelSource(state.head), lineage: lineageOf(state.head) };
  };
  const stageDto = (stage: StageFact): DesignStageDto => ({
    stageRef: stage.ref, parentStageRef: stage.parent, branchId: "main", label: stage.label, candidateId: stage.run,
    modelSource: modelSource(stage.run), recordDigest: digest(`stage:${stage.ref}`), acceptedBy: stage.acceptedBy,
    acceptance: { eventId: `audit-${stage.run}`, occurredAt: stage.acceptedAt, action: "design.accepted", status: "accepted",
      actorId: stage.acceptedBy, authenticated: false, origin: "studio", auditRef: ref(stage.run, "audit-event") },
  });
  return {
    state,
    designHistory(branchId = "main"): DesignHistoryReading {
      if (branchId !== "main") throw new FixtureRefusal(404, "DESIGN_BRANCH_NOT_FOUND", "The design branch does not exist.");
      const branch: DesignBranchDto = { branchId: "main", parentBranch: null, forkStageRef: S0, headStageRef: state.branchHead };
      const inHead = new Set(lineageOf(state.head));
      const candidates: AdmittedCandidateDto[] = state.candidates.map((candidate) => ({
        candidateId: candidate.run, label: candidate.label, summary: candidate.summary,
        baseStageRef: state.runs.get(candidate.run)?.sourceStage ?? null, studyId: candidate.studyId,
        modelSource: modelSource(candidate.run), admittedBy: { actorId: candidate.admittedBy, origin: candidate.admittedBy === "Arch Agent" ? "hub-agent" : "studio" },
        admittedAt: candidate.admittedAt, admissionRef: ref("studio-admissions", `candidate-admission-${candidate.run}`), legacy: null,
        acceptedStageRef: candidate.acceptedStage, continuedFrom: candidate.continuedFrom, inWorkingHeadLineage: inHead.has(candidate.run),
      }));
      return { projectId: FIXTURE_PROJECT, branches: [branch], branchId: "main", stages: historyOrder().map(stageDto),
        candidates, studies: state.studies.map((study) => ({ ...study, candidateIds: [...(study.candidateIds ?? [])] })) };
    },
    workingSource(workspace: WorkingSourceDto["workspace"] = "modeling"): WorkingSourceDto {
      const current = head();
      return { projectId: FIXTURE_PROJECT, workspace, policy: "live", revisionSha256: revision(), head: current, compatible: true,
        source: current.modelSource ?? null, stageRef: current.accepted ? current.sourceStageRef ?? null : null, reason: null, warnings: [] };
    },
    worktrees(): WorktreeGraphDto {
      const current = head();
      const lines: WorktreeLineDto[] = [
        { lineId: `head:${state.head}`, kind: "head", runId: state.head, jobId: null, label: null, baseRunId: null, baseStageRef: null,
          branchId: "main", status: "current", relation: "head", reads: [], writes: [], reconcile: "none", conflicts: [], detail: null, updatedAt: null },
        ...state.running.map((line): WorktreeLineDto => ({
          lineId: line.lineId, kind: "running", runId: null, jobId: line.lineId.replace(/^running:/, ""), label: line.label, baseRunId: line.base,
          baseStageRef: stageOfRun(line.base)?.ref ?? state.runs.get(line.base)?.sourceStage ?? null, branchId: null, status: line.status,
          relation: "ahead", reads: [], writes: [], reconcile: "unknown", conflicts: [], detail: line.detail, updatedAt: "2026-09-25T21:40:00Z" })),
      ];
      return { projectId: FIXTURE_PROJECT, head: current, revisionSha256: revision(), lines, representations: [], warnings: [] };
    },
    workingDraft(): WorkingDraftDto {
      const current = head();
      return { projectId: FIXTURE_PROJECT, revisionSha256: revision(), current: { runId: state.head, sourceStageRef: current.sourceStageRef ?? null,
        branchId: "main", updatedAt: "2026-09-25T22:00:00Z", label: null }, recovery: [], saved: [], managedRunIds: [], localDraft: null };
    },
    /** PUT /api/working-draft: the Working Head moves to an exact finished run. */
    selectWorkingDraft(body: { projectId: string; runId: string | null; baseRevisionSha256: string | null; branchId?: string | null }): WorkingDraftDto {
      if (body.projectId !== FIXTURE_PROJECT) throw new FixtureRefusal(409, "PROJECT_MISMATCH", "The request belongs to another project.");
      if (body.baseRevisionSha256 !== revision()) throw new FixtureRefusal(409, "WORKING_DRAFT_STALE", "The working position changed; read it before selecting another source.");
      if (!body.runId || !state.runs.has(body.runId)) throw new FixtureRefusal(409, "WORKING_DRAFT_UNAVAILABLE", "This run has no exact finished state to continue.");
      state.head = body.runId;
      state.revision += 1;
      return this.workingDraft();
    },
    /** POST /api/candidates/{id}/accept: a Stage from the Working Head, on the line's newest Stage only. */
    accept(candidateId: string, body: { projectId: string; branchId?: string; expectedHeadStageRef: string; label?: string | null }): DesignStageDto {
      if (body.projectId !== FIXTURE_PROJECT) throw new FixtureRefusal(409, "PROJECT_MISMATCH", "The request belongs to another project.");
      if (body.expectedHeadStageRef !== state.branchHead) throw new FixtureRefusal(409, "DESIGN_BRANCH_STALE", "The design branch changed. Review the candidate against its new head.");
      if (state.runs.get(candidateId)?.sourceStage !== body.expectedHeadStageRef) {
        throw new FixtureRefusal(409, "CANDIDATE_STAGE_MISMATCH", "The candidate was not produced from this exact committed Stage.");
      }
      const number = historyOrder().length;
      const stage: StageFact = { ref: ref(candidateId), parent: state.branchHead, run: candidateId, label: body.label ?? `S${number}`,
        acceptedBy: "studio:explicit-user-action", acceptedAt: "2026-09-25T22:10:00Z" };
      state.stages.push(stage);
      state.branchHead = stage.ref;
      // The nearest admitted option on the accepted run's lineage now leads to this Stage.
      const nearest = lineageOf(candidateId).map((run) => state.candidates.find((candidate) => candidate.run === run)).find(Boolean);
      if (nearest && nearest.acceptedStage === null) nearest.acceptedStage = stage.ref;
      state.revision += 1;
      return stageDto(stage);
    },
  };
}

export type DesignTreeFixture = ReturnType<typeof createDesignTreeFixture>;
