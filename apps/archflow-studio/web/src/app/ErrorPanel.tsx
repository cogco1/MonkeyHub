/**
 * How a refusal looks in this shell.
 *
 * The server's `code` and `detail` are shown verbatim, and a
 * `BLOCKED_NEEDS_HUMAN` shows the question it asked and the forms it will
 * accept — in place of the panel's content, never as a toast that scrolls away
 * and never as an empty list.
 */

import type { StudioApiError } from "../api/client";
import { BilingualText } from "../i18n/BilingualText";
import { useT } from "../i18n/useT";

const PROTECTED_TEXT =
  /(`[^`]+`|https?:\/\/[^\s]+|(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+\/[^\s,;]+|[A-Za-z]:\\[^\r\n]+|\/api\/[^\s,;]+|[0-9a-fA-F]{32,}|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|\b[A-Z][A-Z0-9_]{2,}\b|\b(?=[A-Za-z0-9_-]*\d)(?:[A-Za-z0-9]+[-_])+(?:[A-Za-z0-9_-]+)\b|[^\s,;]+\.(?:3dm|json|toml|ya?ml|txt|md)\b)/g;

/**
 * Translate prose while keeping protocol tokens, paths and identifiers verbatim.
 * The English source remains mounted inside every BilingualText layer.
 */
export function BilingualProse({ source }: { source: string }) {
  const parts: Array<{ protected: boolean; text: string }> = [];
  let cursor = 0;

  for (const match of source.matchAll(PROTECTED_TEXT)) {
    const start = match.index;
    if (start > cursor) {
      parts.push({ protected: false, text: source.slice(cursor, start) });
    }
    parts.push({ protected: true, text: match[0] });
    cursor = start + match[0].length;
  }
  if (cursor < source.length) {
    parts.push({ protected: false, text: source.slice(cursor) });
  }

  return (
    <span>
      {parts.map((part, index) =>
        part.protected ? (
          <span key={`${index}:${part.text}`} className="mono" lang="en">
            {part.text}
          </span>
        ) : (
          <BilingualText key={`${index}:${part.text}`} source={part.text} />
        ),
      )}
    </span>
  );
}

export function ErrorPanel({
  error,
  what,
}: {
  error: StudioApiError;
  what?: string;
}) {
  const t = useT();
  if (error.code === "SEMANTIC_EDIT_INVALID") {
    return (
      <div className="error-panel" role="alert">
        <p className="error-panel__detail">{t("error.semanticEditInvalid")}</p>
        <details className="card__details">
          <summary>{t("common.technicalDetails")}</summary>
          <p className="error-panel__head">
            <span className="error-panel__code">{error.code}</span>
            <span className="error-panel__status">HTTP {error.status}</span>
            {what && <span className="error-panel__what">{what}</span>}
          </p>
          <p className="error-panel__detail"><BilingualProse source={error.detail} /></p>
        </details>
      </div>
    );
  }
  return (
    <div className="error-panel" role="alert">
      <p className="error-panel__head">
        <span className="error-panel__code">{error.code}</span>
        {error.status > 0 && (
          <span className="error-panel__status">HTTP {error.status}</span>
        )}
        {what && <span className="error-panel__what">{what}</span>}
      </p>
      <p className="error-panel__detail">
        <BilingualProse source={error.detail} />
      </p>
      {error.question && (
        <p className="error-panel__question">
          <BilingualProse source={error.question} />
        </p>
      )}
      {error.acceptedForms.length > 0 && (
        <ul className="error-panel__forms">
          {error.acceptedForms.map((form) => (
            <li key={form}>
              <code>{form}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
