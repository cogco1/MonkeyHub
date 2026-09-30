/**
 * The two terminal outcomes: the exchange is over and the server has said what
 * it lacks.
 *
 * `MISSING_EDITABLE_CONTROL` — the thing is in the model and the record
 * declares no control for it. That is a missing *system binding*, not a missing
 * answer from the person reading this, and the difference is the whole card:
 * there is no input box, no question and no accepted forms, because asking
 * again is exactly the loop this outcome exists to end. The internal control
 * draft remains available in technical details, not as a form to fill in.
 *
 * `UNSUPPORTED` — no action this server has can express the request, or the
 * clarification stopped advancing. Same shape, no draft.
 */

import { MISSING_EDITABLE_CONTROL, type StudioApiError } from "../../../api/client";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";

export function MissingControlCard({ error }: { error: StudioApiError }) {
  const t = useT();
  const pending = error.pendingIntent;
  const draft = error.authoredControlDraft;
  const missingBinding = error.code === MISSING_EDITABLE_CONTROL;
  return (
    <article className="card card--refusal" aria-live="polite">
      <div className="card__row">
        <p className="card__word card__word--no">
          {missingBinding ? t("terminal.missingControl") : t("terminal.unsupported")}
        </p>
        <p className="verbatim-line">
          <BilingualText source={error.detail} />
        </p>
      </div>
      {(pending || draft) && (
        <details className="card__row card__details">
          <summary>{t("common.technicalDetails")}</summary>
          {pending && (
            <p className="quiet mono">
              {t("terminal.identity", {
                target: pending.targetComponentId ?? "—",
                reason: pending.reasonCode,
              })}
            </p>
          )}
          {draft && (
            <div>
              <p className="label">{t("terminal.draft.title")}</p>
              <p className="quiet">
                <BilingualText source={draft.suggestedAction} />
              </p>
              <dl className="kv">
                <dt>{t("terminal.draft.control")}</dt>
                <dd className="mono">
                  {draft.suggestedElementId}
                  {draft.semanticProperty ? ` · ${draft.semanticProperty}` : ""}
                </dd>
                <dt>{t("terminal.draft.producer")}</dt>
                <dd className="mono">{draft.producer ?? t("evidence.common.none")}</dd>
                <dt>{t("terminal.draft.binding")}</dt>
                <dd className="mono">{draft.binding ?? t("evidence.common.none")}</dd>
                <dt>{t("terminal.draft.unit")}</dt>
                <dd className="mono">{draft.unit ?? t("evidence.common.none")}</dd>
                <dt>{t("terminal.draft.confidence")}</dt>
                <dd className="mono">{draft.confidence}</dd>
                <dt>{t("terminal.draft.provenance")}</dt>
                <dd className="mono">
                  {draft.provenance.length > 0
                    ? draft.provenance.join("; ")
                    : t("evidence.common.none")}
                </dd>
                <dt>{t("terminal.draft.requires")}</dt>
                <dd>
                  <ul className="reflist">
                    {draft.dependencyRequirements.map((line) => (
                      <li key={line}>
                        <BilingualText source={line} />
                      </li>
                    ))}
                  </ul>
                </dd>
              </dl>
              <p className="quiet">{t("terminal.draft.notWritten")}</p>
            </div>
          )}
          {pending && pending.rejectedCandidates.length > 0 && (
            <p className="quiet mono">
              {t("question.ruledOut", { refs: pending.rejectedCandidates.join(", ") })}
            </p>
          )}
        </details>
      )}
    </article>
  );
}
