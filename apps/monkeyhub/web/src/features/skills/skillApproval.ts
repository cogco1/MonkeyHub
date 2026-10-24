/**
 * What the Skills panel holds, and the only things it does: read the list with
 * the project's grants and each skill's last run, open one exact version for
 * review, allow it once or for this version, deny, go back; and read the skill
 * library and import one of its packages, which allows nothing.
 *
 * Kept apart from the view so each step can be exercised without a DOM: the
 * panel's buttons call these actions and nothing else.
 */

import { asStudioApiError, type StudioApiError } from "../../api/project-runtime/error";
import type { MessageKey } from "../../i18n/messages.en";
import {
  HUB_PAGE_APPROVAL, lastRunNeeds, packageKey,
  type GrantScope, type SkillDetail, type SkillGrant, type SkillIndex, type SkillLibrary, type SkillPackage, type SkillRow,
  type SkillRunNeed, type SkillsApi,
} from "../../api/project-runtime/skills";

export interface SkillReview {
  readonly row: SkillRow;
  /** Null while the version's code is being read. */
  readonly detail: SkillDetail | null;
  readonly busy: boolean;
  readonly error: StudioApiError | null;
}

export interface SkillsState {
  readonly index: SkillIndex | null;
  /** The project's grants; null until read, or when this runtime does not list them. */
  readonly grants: readonly SkillGrant[] | null;
  /** Skill id → what its last run tried and was not granted, for a last run that failed for that. */
  readonly needs: Readonly<Record<string, readonly SkillRunNeed[]>>;
  readonly loading: boolean;
  readonly loadError: StudioApiError | null;
  readonly review: SkillReview | null;
  /** The grant given last, said once the list has been read again. */
  readonly granted: { readonly name: string; readonly version: number; readonly scope: GrantScope } | null;
  /** The packages the project can take in; null until read. */
  readonly library: SkillLibrary | null;
  readonly libraryLoading: boolean;
  readonly libraryError: StudioApiError | null;
  /** The package being imported, by its `packageKey`. */
  readonly importing: string | null;
  readonly importError: { readonly key: string; readonly error: StudioApiError } | null;
  /** The import made last; `added` is false when the project already matched the package. */
  readonly imported: { readonly name: string; readonly version: number; readonly added: boolean } | null;
}

export const initialSkillsState: SkillsState = {
  index: null, grants: null, needs: {}, loading: true, loadError: null, review: null, granted: null,
  library: null, libraryLoading: true, libraryError: null, importing: null, importError: null, imported: null,
};

type SetState = (update: (state: SkillsState) => SkillsState) => void;

export interface SkillsActions {
  load(): Promise<void>;
  review(row: SkillRow): Promise<void>;
  /** Grant exactly the version and code hash shown: one run, or this version until revoked. */
  grant(projectId: string, detail: SkillDetail, scope: GrantScope): Promise<void>;
  /** Close the review; nothing is sent or recorded. */
  deny(): void;
  /** One of the review's three buttons: grant once, grant this version, or deny. */
  answer(choice: ReviewChoice, projectId: string | null, detail: SkillDetail | null): Promise<void>;
  loadLibrary(): Promise<void>;
  /** Register a package as a version of the project's skill, then read the library and the list again. */
  importPackage(projectId: string, row: SkillPackage): Promise<void>;
}

export type ReviewChoice = GrantScope | "deny";

/** The review's answers, in the order they are offered; only the first two send anything. */
export const REVIEW_CHOICES = [
  { choice: "once", label: "skills.confirm.allowOnce" },
  { choice: "version", label: "skills.confirm.allowVersion" },
  { choice: "deny", label: "skills.confirm.deny" },
] as const satisfies readonly { choice: ReviewChoice; label: MessageKey }[];

/** What each runnable skill's last run needed; a skill whose runs cannot be read says nothing. */
async function readNeeds(api: SkillsApi, index: SkillIndex): Promise<Record<string, readonly SkillRunNeed[]>> {
  const needs: Record<string, readonly SkillRunNeed[]> = {};
  await Promise.all(index.skills
    .filter((row) => row.runnable !== false && typeof row.codeSha256 === "string")
    .map(async (row) => {
      try {
        const found = lastRunNeeds(await api.runs(row.id));
        if (found) needs[row.id] = found;
      } catch { /* An older runtime without the runs route, or a skill that never ran. */ }
    }));
  return needs;
}

export function skillsActions(api: SkillsApi, setState: SetState): SkillsActions {
  const load = async () => {
    setState((state) => ({ ...state, loading: true, loadError: null }));
    try {
      // A runtime that cannot list grants still lists its skills; the rows then say only what they carry.
      const [index, grants] = await Promise.all([api.list(), api.approvals().then((body) => body.approvals, () => null)]);
      const needs = await readNeeds(api, index);
      setState((state) => ({ ...state, index, grants, needs, loading: false }));
    } catch (cause) {
      setState((state) => ({ ...state, loading: false, loadError: asStudioApiError(cause) }));
    }
  };
  const loadLibrary = async () => {
    setState((state) => ({ ...state, libraryLoading: true, libraryError: null }));
    try {
      const library = await api.library();
      setState((state) => ({ ...state, library, libraryLoading: false }));
    } catch (cause) {
      setState((state) => ({ ...state, libraryLoading: false, libraryError: asStudioApiError(cause) }));
    }
  };
  const deny = () => setState((state) => ({ ...state, review: null }));
  const grant = async (projectId: string, detail: SkillDetail, scope: GrantScope) => {
    const codeSha256 = detail.codeSha256;
    if (!codeSha256) return;
    const shown = (state: SkillsState): state is SkillsState & { review: SkillReview } => state.review?.detail === detail;
    setState((state) => shown(state) ? { ...state, review: { ...state.review, busy: true, error: null } } : state);
    try {
      // Exactly the version and the hash of the code that was shown.
      await api.approve(detail.id, { projectId, version: detail.version, codeSha256, method: HUB_PAGE_APPROVAL, scope });
    } catch (cause) {
      setState((state) => shown(state) ? { ...state, review: { ...state.review, busy: false, error: asStudioApiError(cause) } } : state);
      // A refusal such as SKILL_CODE_CHANGED means the list behind may be stale too.
      await load();
      return;
    }
    setState((state) => ({ ...state, review: shown(state) ? null : state.review, granted: { name: detail.name, version: detail.version, scope } }));
    await load();
  };
  return {
    load,
    grant,
    deny,
    loadLibrary,
    async importPackage(projectId, row) {
      const key = packageKey(row);
      setState((state) => ({ ...state, importing: key, importError: null, imported: null, granted: null }));
      try {
        const { imported, skill } = await api.importPackage({ projectId, source: row.source, name: row.name });
        setState((state) => ({ ...state, importing: null, imported: { name: skill.name, version: skill.version, added: imported } }));
      } catch (cause) {
        setState((state) => ({ ...state, importing: null, importError: { key, error: asStudioApiError(cause) } }));
        // Nothing was added; a package that changed or went away since it was listed says so on the next read.
        await loadLibrary();
        return;
      }
      // The new version is on the list, waiting for a grant, and the library says the project holds it.
      await Promise.all([load(), loadLibrary()]);
    },
    async answer(choice, projectId, detail) {
      if (choice === "deny") deny();
      else if (detail && projectId !== null) await grant(projectId, detail, choice);
    },
    async review(row) {
      setState((state) => ({ ...state, granted: null, imported: null, review: { row, detail: null, busy: true, error: null } }));
      try {
        const detail = await api.read(row.id, row.version);
        setState((state) => state.review?.row === row ? { ...state, review: { row, detail, busy: false, error: null } } : state);
      } catch (cause) {
        setState((state) => state.review?.row === row
          ? { ...state, review: { row, detail: null, busy: false, error: asStudioApiError(cause) } } : state);
      }
    },
  };
}
