/**
 * Any refusal that is not a question: the server's code and detail, verbatim,
 * with the route that answered.
 */

import type { StudioApiError } from "../../../api/client";
import { ErrorPanel } from "../../../app/ErrorPanel";

export function RefusalCard({
  error,
  what,
}: {
  error: StudioApiError;
  what: string;
}) {
  return (
    <article className="card card--refusal">
      <div className="card__row">
        <p className="card__word card__word--no">The server refused</p>
        <ErrorPanel error={error} what={what} />
      </div>
    </article>
  );
}
