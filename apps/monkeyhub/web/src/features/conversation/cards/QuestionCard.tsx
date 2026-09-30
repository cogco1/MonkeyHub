/**
 * `NEEDS_CLARIFICATION`, as a question rather than an error: the server could
 * not turn the utterance into a proposal and says what it needs.
 *
 * Two kinds of reply, and the difference matters. **The candidates are
 * answers**: choosing one moves the selection and re-asks the original request
 * against it, carrying the pending intent's `continuationToken` — the exchange
 * continues, and the target advances in the same click. **The accepted forms
 * are templates**: they fill the composer, because a form is a shape to type a
 * number into and sending it verbatim would only earn the same question back.
 *
 * The question and its choices stay visible. Compiler slots and grammar
 * templates are available on demand, not presented as work for the architect.
 */

import { useState } from "react";
import type { PendingIntentDto } from "../../../api/generated";
import type { StudioApiError } from "../../../api/client";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";

export interface Choice {
  componentId: string;
  elementId: string | null;
}

export function QuestionCard({
  error,
  onReply,
  onChoose,
}: {
  error: StudioApiError;
  /** Put a form in the composer to type into. */
  onReply(text: string): void;
  /** Answer with one of the server's own candidates, continuing the exchange. */
  onChoose(choice: Choice): void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState("");
  const pending: PendingIntentDto | null = error.pendingIntent;
  const candidates = pending?.candidates ?? [];
  const shown = expanded ? candidates.filter((candidate) => `${candidate.label} ${candidate.componentId} ${candidate.elementId ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())) : candidates.slice(0, 4);
  return (
    <article className="card card--question" aria-live="polite">
      <div className="card__row">
        <p className="card__word card__word--ask">{t("question.title")}</p>
        <p className="verbatim-line">
          <BilingualText source={error.question ?? error.detail} />
        </p>
      </div>
      {candidates.length > 0 && (
        <div className="card__row chips">
          {expanded && <input aria-label="搜索澄清对象" placeholder="搜索对象" value={search} onChange={(event) => setSearch(event.target.value)} />}
          {shown.map((candidate) => (
            <button
              key={candidate.ref}
              type="button"
              className="chip"
              onClick={() =>
                onChoose({
                  componentId: candidate.componentId,
                  elementId: candidate.elementId ?? null,
                })
              }
            >
              <span className="mono">{candidate.label}</span>
            </button>
          ))}
          {candidates.length > 4 && <button type="button" className="btn btn--small" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : `查看全部 ${candidates.length} 项`}</button>}
        </div>
      )}
      {(error.question || error.acceptedForms.length > 0 || pending) && (
        <details className="card__row card__details">
          <summary>{t("common.technicalDetails")}</summary>
          {error.question && (
            <p className="quiet">
              <BilingualText source={error.detail} />
            </p>
          )}
          {pending && pending.missingSlots.length > 0 && (
            <p className="quiet mono">
              {t("question.stillOpen", { slots: pending.missingSlots.join(", ") })}
            </p>
          )}
          <div className="chips">
            {error.acceptedForms.map((form) => (
              <button
                key={form}
                type="button"
                className="chip"
                onClick={() => onReply(form)}
              >
                <code>{form}</code>
              </button>
            ))}
          </div>
          {pending && pending.rejectedCandidates.length > 0 && (
            <p className="quiet mono">
              {t("question.ruledOut", {
                refs: pending.rejectedCandidates.join(", "),
              })}
            </p>
          )}
        </details>
      )}
    </article>
  );
}
