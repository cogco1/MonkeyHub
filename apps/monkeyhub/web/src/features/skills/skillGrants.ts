/**
 * What the "What the AI may do" page holds (S-1), and the only things it does:
 * read what a person allowed skills to do on this computer and which installed
 * skills cannot be used and why, withdraw a grant, read again. The grants are the Hub's own, kept beside its settings, so no project
 * is read and none has to be running.
 *
 * Kept apart from the view so each step can be exercised without a DOM.
 */

import { createClient } from "../../api/generated/client";
import {
  readSkillGrantsApiSettingsSkillGrantsGet, readSkillProblemsApiSettingsSkillProblemsGet,
  revokeSkillGrantApiSettingsSkillGrantsGrantIdRevokePost,
  type HubError, type SkillGrantDto, type SkillProblemDto,
} from "../../api/generated";

export type SkillGrant = SkillGrantDto;
/** An installed skill that cannot be used right now: which, where it is, what kind of problem, the technical line. */
export type SkillProblem = SkillProblemDto;

export interface SkillGrantsApi {
  list(): Promise<readonly SkillGrant[]>;
  /** The skills that cannot be used; a Hub that cannot say leaves the page without that section. */
  problems(): Promise<readonly SkillProblem[]>;
  revoke(grantId: string): Promise<SkillGrant>;
}

function failure(result: { response?: Response; error?: unknown }): HubError {
  const error = result.error as { code?: unknown; detail?: unknown } | undefined;
  return {
    code: typeof error?.code === "string" ? error.code : `HTTP_${result.response?.status ?? 0}`,
    detail: typeof error?.detail === "string" ? error.detail : `HTTP ${result.response?.status ?? 0}`,
  };
}

async function body<T>(request: Promise<unknown>): Promise<T> {
  const result = await request as { response?: Response; data?: T; error?: unknown };
  if (!result.response?.ok || result.error !== undefined || result.data === undefined) throw failure(result);
  return result.data;
}

export function createSkillGrantsApi(baseUrl: string): SkillGrantsApi {
  const client = createClient({ baseUrl });
  return {
    list: async () => (await body<{ grants?: SkillGrant[] }>(readSkillGrantsApiSettingsSkillGrantsGet({ client }))).grants ?? [],
    problems: () => body<SkillProblem[]>(readSkillProblemsApiSettingsSkillProblemsGet({ client })),
    revoke: (grantId) => body<SkillGrant>(revokeSkillGrantApiSettingsSkillGrantsGrantIdRevokePost({ client, path: { grant_id: grantId } })),
  };
}

export interface GrantsState {
  /** Still in force first, each group newest first; null until read. */
  readonly grants: readonly SkillGrant[] | null;
  /** The skills that cannot be used right now; null until read, or when the Hub could not say. */
  readonly problems: readonly SkillProblem[] | null;
  readonly loading: boolean;
  readonly loadError: HubError | null;
  /** The grant being withdrawn. */
  readonly revoking: string | null;
  readonly revokeError: { readonly grantId: string; readonly error: HubError } | null;
}

export const initialGrantsState: GrantsState = {
  grants: null, problems: null, loading: true, loadError: null, revoking: null, revokeError: null,
};

type SetState = (update: (state: GrantsState) => GrantsState) => void;

export interface GrantsActions {
  load(): Promise<void>;
  revoke(grantId: string): Promise<void>;
}

/** A one-run grant that has run out shows as used; a withdrawn one as withdrawn. */
export function grantStatus(grant: SkillGrant, now: Date = new Date()): "active" | "used" | "revoked" {
  if (grant.revokedAt) return "revoked";
  const expires = grant.once?.expiresAt;
  return expires && new Date(expires).getTime() <= now.getTime() ? "used" : "active";
}

const order = (a: SkillGrant, b: SkillGrant) =>
  Number(grantStatus(a) !== "active") - Number(grantStatus(b) !== "active") || b.grantedAt.localeCompare(a.grantedAt);

const asHubError = (cause: unknown): HubError => {
  const error = cause as Partial<HubError> | undefined;
  return { code: typeof error?.code === "string" ? error.code : "HUB_UNAVAILABLE",
    detail: typeof error?.detail === "string" ? error.detail : String(cause) };
};

export function grantsActions(api: SkillGrantsApi, setState: SetState): GrantsActions {
  const load = async () => {
    setState((state) => ({ ...state, loading: true, loadError: null }));
    try {
      const [listed, problems] = await Promise.all([api.list(), api.problems().catch(() => null)]);
      const grants = [...listed].sort(order);
      setState((state) => ({ ...state, grants, problems, loading: false }));
    } catch (cause) {
      setState((state) => ({ ...state, loading: false, loadError: asHubError(cause) }));
    }
  };
  return {
    load,
    async revoke(grantId) {
      setState((state) => ({ ...state, revoking: grantId, revokeError: null }));
      try {
        await api.revoke(grantId);
        setState((state) => ({ ...state, revoking: null }));
      } catch (cause) {
        setState((state) => ({ ...state, revoking: null, revokeError: { grantId, error: asHubError(cause) } }));
      }
      // Withdrawn here or elsewhere, the list says what holds now.
      await load();
    },
  };
}
