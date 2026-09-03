/**
 * How a refusal looks in this shell.
 *
 * The server's `code` and `detail` are shown verbatim, and a
 * `BLOCKED_NEEDS_HUMAN` shows the question it asked and the forms it will
 * accept — in place of the panel's content, never as a toast that scrolls away
 * and never as an empty list.
 */

import type { StudioApiError } from "../api/client";

export function ErrorPanel({
  error,
  what,
}: {
  error: StudioApiError;
  what?: string;
}) {
  return (
    <div className="error-panel" role="alert">
      <p className="error-panel__head">
        <span className="error-panel__code">{error.code}</span>
        {error.status > 0 && (
          <span className="error-panel__status">HTTP {error.status}</span>
        )}
        {what && <span className="error-panel__what">{what}</span>}
      </p>
      <p className="error-panel__detail">{error.detail}</p>
      {error.question && (
        <p className="error-panel__question">{error.question}</p>
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
