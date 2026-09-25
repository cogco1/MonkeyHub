/**
 * What the Design Tree reads from the Project Runtime.
 *
 * `GET /api/design-history` gains two arrays in #294 slice S2
 * (`docs/2026-09-25-candidate-admission-audit.md` §4.2), implemented on
 * `codex/294-admission-record`: `candidates[]`, the admitted Candidates only,
 * and `studies[]`. Until the generated DTO carries them they are typed here,
 * with the field names of that branch's transport, and a response without
 * them reads as "no admitted Candidate yet": the tree then shows Stages,
 * Current and running work, which is honest (§3 (d)).
 * `GET /api/working-source` names the Working Head and its lineage;
 * `GET /api/worktrees` names the running and queued work.
 */
import type { DesignHistoryDto, ModelSourceDto, WorkingSourceDto, WorktreeGraphDto } from "../../api/generated";

/** Which retained fact makes a run an admitted Candidate when it has no admission record (§4.4). */
export type CandidateLegacySource = "stage" | "working-copy" | "episode";

/** Who admitted a Candidate, and through which door (Studio UI, Hub Agent, retroactive review). */
export interface CandidateActorDto {
  readonly actorId: string;
  readonly origin?: string | null;
  readonly authenticated?: boolean | null;
}

/** One admitted Candidate. `candidateId` is the id of its result run. */
export interface AdmittedCandidateDto {
  readonly candidateId: string;
  /** "rejected" only when a reader asks for retained rejections; the tree never draws those. */
  readonly outcome?: "admitted" | "rejected";
  readonly label?: string | null;
  readonly summary?: string | null;
  /** The Stage the Candidate's work started from (its delta's source Stage). */
  readonly baseStageRef?: string | null;
  readonly studyId?: string | null;
  /** Anchors the preview (#292); the tree shows a letter until one exists. */
  readonly modelSource?: ModelSourceDto | null;
  readonly admittedBy?: CandidateActorDto | string | null;
  readonly admittedAt?: string | null;
  readonly admissionRef?: string | null;
  readonly legacy?: CandidateLegacySource | null;
  /** The Stage later accepted from this Candidate or one of its continuations. */
  readonly acceptedStageRef?: string | null;
  /** The admitted Candidate this one continued, when it did not start from a Stage. */
  readonly continuedFrom?: string | null;
  readonly inWorkingHeadLineage?: boolean;
}

/** One request for alternatives, such as "Entrance Study · 3 options". */
export interface DesignStudyDto {
  readonly id: string;
  readonly label?: string | null;
  /** The run its options were built from: a Stage's run, an admitted option, or work in between. */
  readonly baseRunId?: string | null;
  /** The Stage it started from; null under the Unstaged root. */
  readonly baseStageRef?: string | null;
  /** Its admitted Candidates, in the order they were asked for. */
  readonly candidateIds?: readonly string[];
}

/** `GET /api/design-history` as the tree reads it. */
export type DesignHistoryReading = DesignHistoryDto & {
  readonly candidates?: readonly AdmittedCandidateDto[];
  readonly studies?: readonly DesignStudyDto[];
};

/** Everything one tree is built from, read together. */
export interface DesignTreeSource {
  readonly projectId: string;
  /** Every design line's Stages; the Working Head's line first. */
  readonly history: DesignHistoryReading;
  readonly workingSource: WorkingSourceDto;
  /** Null when the Worktree Graph could not be read: the tree still shows its facts. */
  readonly worktrees: WorktreeGraphDto | null;
}
