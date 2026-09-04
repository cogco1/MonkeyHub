/**
 * `BLOCKED_NEEDS_HUMAN`, as a question rather than an error: the server could
 * not turn the utterance into a proposal and says what it needs. The question
 * is verbatim; the accepted forms become quick replies that fill the composer
 * and send nothing.
 */

import type { StudioApiError } from "../../../api/client";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";

export function QuestionCard({
  error,
  onReply,
}: {
  error: StudioApiError;
  onReply(text: string): void;
}) {
  const t = useT();
  return (
    <article className="card card--question">
      <div className="card__row">
        <p className="card__word card__word--ask">{t("question.title")}</p>
        <p className="verbatim-line">
          <BilingualText source={error.question ?? error.detail} />
        </p>
        {error.question && (
          <p className="quiet">
            <BilingualText source={error.detail} />
          </p>
        )}
      </div>
      {error.acceptedForms.length > 0 && (
        <div className="card__row chips">
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
      )}
    </article>
  );
}
