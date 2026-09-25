/**
 * What the Design Tree reads from the Project Runtime.
 *
 * The generated client types are the contract; the tree adds no DTO of its
 * own. `GET /api/design-history` names every design line's Stages and, with
 * #294 (`candidate-admission`), the project's admitted Candidates
 * (`candidates[]`: admitted only, unless a reader asks for `include=rejected`)
 * and their Studies (`studies[]`). `GET /api/working-source` names the
 * Working Head and its lineage; `GET /api/worktrees` names the running and
 * queued work and each line's admission verdict.
 */
import type { DesignHistoryDto, WorkingSourceDto, WorktreeGraphDto } from "../../api/generated";

/** Everything one tree is built from, read together. */
export interface DesignTreeSource {
  readonly projectId: string;
  /** Every design line's Stages, admitted Candidates and Studies; the Working Head's line first. */
  readonly history: DesignHistoryDto;
  readonly workingSource: WorkingSourceDto;
  /** Null when the Worktree Graph could not be read: the tree still shows its facts. */
  readonly worktrees: WorktreeGraphDto | null;
}
