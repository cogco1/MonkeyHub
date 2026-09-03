/**
 * What "the run is not over yet" means, defined once for this shell.
 *
 * Two panels ask the question — the poller that decides whether to poll again,
 * and the validation panel that decides whether a verdict is readable at all —
 * and two copies of this set could drift into disagreeing about a status the
 * server added later.
 */

/** A job in one of these states has not finished; its verdict is not readable. */
export const IN_FLIGHT = new Set(["queued", "running"]);
