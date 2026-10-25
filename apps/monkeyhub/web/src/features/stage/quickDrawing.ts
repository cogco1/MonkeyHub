/**
 * The model page's one-click drawings (S15): which answer still belongs to the model on screen, and what four
 * elevations drawn at once came to when some of them failed.
 */

/**
 * Requests numbered as they start; a change of the model on screen ends every one started before it. An answer
 * whose request has ended belongs to another model and is not shown as this one's.
 */
export class QuickTickets {
  private current = 0;
  issue(): number { this.current += 1; return this.current; }
  /** The model on screen changed: what was asked of the one before is no longer this page's to show. */
  expire(): void { this.current += 1; }
  isCurrent(ticket: number): boolean { return ticket === this.current; }
}

export interface ElevationSettled<Page> {
  pages: { page: Page; label: string }[];
  failed: { label: string; direction: string; error: unknown }[];
}

/**
 * Four elevations asked for together: each one drawn is kept, each one refused is named with its direction,
 * so one that failed neither hides the others nor makes them look failed.
 */
export function settleElevations<Document, Page>(
  results: readonly PromiseSettledResult<Document>[],
  views: readonly { label: string; direction: string }[],
  pageOf: (document: Document) => Page,
): ElevationSettled<Page> {
  const settled: ElevationSettled<Page> = { pages: [], failed: [] };
  results.forEach((result, index) => {
    const { label, direction } = views[index];
    if (result.status === "fulfilled") settled.pages.push({ page: pageOf(result.value), label });
    else settled.failed.push({ label, direction, error: result.reason });
  });
  return settled;
}
