/**
 * What the Skill permissions page holds, and the only things it does: read
 * every grant of the project, revoke an active one, read them again.
 *
 * Kept apart from the view so each step can be exercised without a DOM.
 */

import { asStudioApiError, type StudioApiError } from "../../api/project-runtime/error";
import type { SkillGrant, SkillsApi } from "../../api/project-runtime/skills";

export interface GrantsState {
  readonly projectId: string | null;
  /** Newest first; null until read. */
  readonly grants: readonly SkillGrant[] | null;
  /** Skill id → name, from the skill list; a grant whose skill is not listed shows its id. */
  readonly names: Readonly<Record<string, string>>;
  readonly loading: boolean;
  readonly loadError: StudioApiError | null;
  /** The grant being revoked. */
  readonly revoking: string | null;
  readonly revokeError: { readonly approvalId: string; readonly error: StudioApiError } | null;
}

export const initialGrantsState: GrantsState = {
  projectId: null, grants: null, names: {}, loading: true, loadError: null, revoking: null, revokeError: null,
};

type SetState = (update: (state: GrantsState) => GrantsState) => void;

export interface GrantsActions {
  load(): Promise<void>;
  revoke(projectId: string, approvalId: string): Promise<void>;
}

const newestFirst = (a: SkillGrant, b: SkillGrant) => b.approvedAt.localeCompare(a.approvedAt);

export function grantsActions(api: SkillsApi, setState: SetState): GrantsActions {
  const load = async () => {
    setState((state) => ({ ...state, loading: true, loadError: null }));
    try {
      const [body, index] = await Promise.all([api.approvals(), api.list().catch(() => null)]);
      const names = Object.fromEntries((index?.skills ?? []).map((row) => [row.id, row.name]));
      setState((state) => ({ ...state, projectId: body.projectId, grants: [...body.approvals].sort(newestFirst), names, loading: false }));
    } catch (cause) {
      setState((state) => ({ ...state, loading: false, loadError: asStudioApiError(cause) }));
    }
  };
  return {
    load,
    async revoke(projectId, approvalId) {
      setState((state) => ({ ...state, revoking: approvalId, revokeError: null }));
      try {
        await api.revoke(approvalId, projectId);
        setState((state) => ({ ...state, revoking: null }));
      } catch (cause) {
        setState((state) => ({ ...state, revoking: null, revokeError: { approvalId, error: asStudioApiError(cause) } }));
      }
      // Revoked here or elsewhere, the list says what holds now.
      await load();
    },
  };
}
