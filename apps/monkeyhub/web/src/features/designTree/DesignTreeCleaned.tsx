/**
 * The superseded drafts the project cleaned into its trash (#575), in the
 * inspector of the drafts card that holds them: quiet like the card, each
 * draft's words beside its Restore while it can still be restored. A restored
 * draft returns where it was and is never cleaned again; the rest are deleted
 * once their days are up.
 */
import type { StudioApiError } from "../../api/project-runtime/client";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import type { CleanedDraft } from "./model";
import { refusalWords, whenText, type TreeWords } from "./words";

const DAY_MS = 24 * 60 * 60 * 1000;

/** `now` is for tests; the inspector reads the clock. */
export function CleanedDrafts({ drafts, words, busy, canRestore, restoring, refused, onRestore, now = Date.now() }: {
  drafts: readonly CleanedDraft[];
  words: Pick<TreeWords, "cleanedBy">;
  busy: boolean;
  canRestore: boolean;
  /** The draft a Restore is on the way for. */
  restoring: string | null;
  refused: { readonly runId: string; readonly error: StudioApiError } | null;
  onRestore(runId: string): void;
  now?: number;
}) {
  const t = useT();
  const { language } = usePreferences();
  if (!drafts.length) return null;
  return <section className="design-tree-cleaned" aria-label={t("designTree.cleaned.title")}>
    <h3 className="design-tree-cleaned__title">{t("designTree.cleaned.title")}</h3>
    <ul>{drafts.map((draft) => {
      // Days are counted up, as a person counts what is left; the last day says the draft goes within it.
      const remaining = Date.parse(draft.expiresAt) - now;
      const when = whenText(draft.trashedAt, language) ?? draft.trashedAt;
      return <li key={draft.runId} data-run={draft.runId}>
        <div className="design-tree-cleaned__words">
          <strong>{draft.label ?? t("designTree.draft.unnamed")}</strong>
          <span>{words.cleanedBy(draft)}</span>
          <span>{remaining >= DAY_MS ? t("designTree.cleaned.when", { when, days: Math.ceil(remaining / DAY_MS) })
            : t("designTree.cleaned.lastDay", { when })}</span>
        </div>
        <button type="button" className="btn btn--small" data-action="restore" disabled={busy || !canRestore}
          onClick={() => onRestore(draft.runId)}>
          {restoring === draft.runId ? t("designTree.action.restoringDraft") : t("designTree.action.restoreDraft")}</button>
        {refused?.runId === draft.runId && <p className="design-tree-inspector__refusal" role="alert">{refusalWords(t, language, refused.error)}</p>}
      </li>;
    })}</ul>
  </section>;
}
